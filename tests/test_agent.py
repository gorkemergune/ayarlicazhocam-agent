"""Tests for the agent orchestration layer (Phase 5).

Providers are mocked with a scripted fake; tools are a fake registry, so these
tests exercise the loop mechanics only — no real SDK, no database, no business
logic (already covered in earlier phases).
"""

from __future__ import annotations

import json

import pytest

import agent.providers as providers_module
from agent import (
    AgentResult,
    Orchestrator,
    Provider,
    ProviderError,
    ProviderResponse,
    ToolCall,
    get_provider,
)


class ScriptedProvider(Provider):
    """Returns queued ProviderResponses; falls back to a plain final answer."""

    def __init__(self, responses, name="fake", raise_error=False):
        self.name = name
        self._responses = list(responses)
        self.generate_calls = 0
        self._raise_error = raise_error

    def generate(self, messages, tools):
        self.generate_calls += 1
        if self._raise_error:
            raise ProviderError("provider down")
        if self._responses:
            return self._responses.pop(0)
        return ProviderResponse(text="final answer")


def ok_tool(**kwargs):
    return {"success": True, "data": kwargs, "message": "ok", "error": None}


def boom_tool(**kwargs):
    raise RuntimeError("kaboom")  # simulate an unexpected tool crash


REGISTRY = {"create_task": ok_tool, "get_tasks": ok_tool, "update_task": ok_tool}


def _tool_messages(result: AgentResult):
    return [m for m in result.messages if m.get("role") == "tool"]


def _envelope_of(tool_message):
    return json.loads(tool_message["content"])


# --- no tool call ----------------------------------------------------------

def test_no_tool_call():
    provider = ScriptedProvider([ProviderResponse(text="hello, no tools")])
    result = Orchestrator(provider, registry=REGISTRY).run("hi")

    assert result.success is True
    assert result.final_message == "hello, no tools"
    assert result.tool_calls == []
    assert provider.generate_calls == 1


# --- single tool call ------------------------------------------------------

def test_single_tool_call():
    provider = ScriptedProvider(
        [
            ProviderResponse(
                tool_calls=[ToolCall("c1", "create_task", {"title": "x"})]
            ),
            ProviderResponse(text="done"),
        ]
    )
    result = Orchestrator(provider, registry=REGISTRY).run("add x")

    assert result.success is True
    assert result.final_message == "done"
    assert result.tool_calls == ["create_task"]
    # One tool result was fed back, unmodified as the full envelope.
    tool_msgs = _tool_messages(result)
    assert len(tool_msgs) == 1
    assert _envelope_of(tool_msgs[0])["success"] is True
    assert provider.generate_calls == 2


# --- multiple sequential tool calls ----------------------------------------

def test_multiple_sequential_tool_calls():
    provider = ScriptedProvider(
        [
            ProviderResponse(tool_calls=[ToolCall("c1", "get_tasks", {})]),
            ProviderResponse(
                tool_calls=[ToolCall("c2", "create_task", {"title": "y"})]
            ),
            ProviderResponse(text="all done"),
        ]
    )
    result = Orchestrator(provider, registry=REGISTRY).run("do two things")

    assert result.success is True
    assert result.tool_calls == ["get_tasks", "create_task"]
    assert result.final_message == "all done"
    # Execution order preserved in the log.
    assert [t.order for t in result.log.tools] == [1, 2]


# --- unknown tool ----------------------------------------------------------

def test_unknown_tool():
    provider = ScriptedProvider(
        [
            ProviderResponse(tool_calls=[ToolCall("c1", "nonexistent", {})]),
            ProviderResponse(text="handled"),
        ]
    )
    result = Orchestrator(provider, registry=REGISTRY).run("call ghost")

    assert result.success is True  # loop recovers and continues
    envelope = _envelope_of(_tool_messages(result)[0])
    assert envelope["success"] is False
    assert envelope["error"]["code"] == "UNKNOWN_TOOL"


# --- malformed arguments ---------------------------------------------------

def test_malformed_arguments():
    provider = ScriptedProvider(
        [
            # arguments is a string, not an object -> malformed
            ProviderResponse(tool_calls=[ToolCall("c1", "create_task", "not-json")]),
            ProviderResponse(text="ok"),
        ]
    )
    result = Orchestrator(provider, registry=REGISTRY).run("bad args")

    envelope = _envelope_of(_tool_messages(result)[0])
    assert envelope["success"] is False
    assert envelope["error"]["code"] == "MALFORMED_ARGUMENTS"


# --- tool failure ----------------------------------------------------------

def test_tool_execution_failure():
    registry = {"create_task": boom_tool}
    provider = ScriptedProvider(
        [
            ProviderResponse(
                tool_calls=[ToolCall("c1", "create_task", {"title": "x"})]
            ),
            ProviderResponse(text="recovered"),
        ]
    )
    result = Orchestrator(provider, registry=registry).run("crash it")

    envelope = _envelope_of(_tool_messages(result)[0])
    assert envelope["success"] is False
    assert envelope["error"]["code"] == "TOOL_EXECUTION_ERROR"
    # Failure is logged as unsuccessful but the run still completes.
    assert result.log.tools[0].success is False
    assert result.success is True


# --- depth limit -----------------------------------------------------------

def test_depth_limit():
    # Provider always asks for another tool -> must be capped.
    always = ScriptedProvider([])  # empty -> would return final; override below

    class AlwaysToolProvider(ScriptedProvider):
        def generate(self, messages, tools):
            self.generate_calls += 1
            return ProviderResponse(
                tool_calls=[ToolCall(f"c{self.generate_calls}", "get_tasks", {})]
            )

    provider = AlwaysToolProvider([])
    result = Orchestrator(provider, registry=REGISTRY, max_tool_depth=5).run("loop")

    assert result.success is False
    assert result.error == "MAX_TOOL_DEPTH_EXCEEDED"
    assert len(result.log.tools) == 5  # exactly max_tool_depth executions
    assert provider.generate_calls == 6  # 5 rounds + the one that trips the cap


# --- provider abstraction --------------------------------------------------

def test_provider_abstraction_is_interchangeable():
    script = [
        ProviderResponse(tool_calls=[ToolCall("c1", "get_tasks", {})]),
        ProviderResponse(text="answer"),
    ]
    for provider_name in ("groq", "gemini"):
        provider = ScriptedProvider(list(script), name=provider_name)
        result = Orchestrator(provider, registry=REGISTRY).run("q")
        assert result.provider == provider_name
        assert result.final_message == "answer"
        assert result.tool_calls == ["get_tasks"]


def test_provider_unavailable_is_graceful():
    provider = ScriptedProvider([], raise_error=True)
    result = Orchestrator(provider, registry=REGISTRY).run("hi")
    assert result.success is False
    assert result.error == "PROVIDER_ERROR"


def test_get_provider_selects_by_env(monkeypatch):
    class DummyGroq(Provider):
        name = "groq"

        def __init__(self, **kwargs):
            pass

        def generate(self, messages, tools):
            return ProviderResponse(text="x")

    class DummyGemini(DummyGroq):
        name = "gemini"

    monkeypatch.setattr(providers_module, "GroqProvider", DummyGroq)
    monkeypatch.setattr(providers_module, "GeminiProvider", DummyGemini)

    monkeypatch.setenv("MODEL_PROVIDER", "gemini")
    assert get_provider().name == "gemini"
    monkeypatch.setenv("MODEL_PROVIDER", "groq")
    assert get_provider().name == "groq"


def test_get_provider_unknown_raises(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "does-not-exist")
    with pytest.raises(ProviderError):
        get_provider()
