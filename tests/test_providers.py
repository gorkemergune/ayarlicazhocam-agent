"""Tests for the real provider layer (Phase 6).

No real API calls: Groq/Gemini clients are faked with SDK-shaped objects and
injected via the ``client=`` constructor argument. Covers provider selection,
request conversion, tool-call/response parsing, usage parsing, and
provider-specific error mapping.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import agent.providers as P
from agent.providers import (
    ERR_CONFIG,
    ERR_INVALID_API_KEY,
    ERR_MALFORMED_RESPONSE,
    ERR_MODEL_UNAVAILABLE,
    ERR_PROVIDER_UNAVAILABLE,
    ERR_RATE_LIMIT,
    ERR_TIMEOUT,
    GeminiProvider,
    GroqProvider,
    OllamaProvider,
    ProviderError,
    Usage,
    get_provider,
)


# --- provider selection ----------------------------------------------------

def test_get_provider_selects_by_env(monkeypatch):
    class DummyGroq(P.Provider):
        name = "groq"

        def __init__(self, **kwargs):
            pass

        def generate(self, messages, tools):
            return P.ProviderResponse(text="x")

    class DummyGemini(DummyGroq):
        name = "gemini"

    monkeypatch.setattr(P, "GroqProvider", DummyGroq)
    monkeypatch.setattr(P, "GeminiProvider", DummyGemini)

    monkeypatch.setenv("MODEL_PROVIDER", "gemini")
    assert get_provider().name == "gemini"
    monkeypatch.setenv("MODEL_PROVIDER", "groq")
    assert get_provider().name == "groq"


def test_get_provider_unknown_raises(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "nope")
    with pytest.raises(ProviderError) as exc:
        get_provider()
    assert exc.value.code == ERR_CONFIG


def test_missing_key_is_config_error(monkeypatch):
    # No client injected and no key -> meaningful config error (not a crash).
    # (If groq isn't installed we get ERR_CONFIG "not installed"; if it is, the
    # missing key gives ERR_CONFIG "not set". Either way: a clear config error.)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(ProviderError) as exc:
        GroqProvider(api_key=None)
    assert exc.value.code == ERR_CONFIG


# --- Groq: request conversion & parsing ------------------------------------

class FakeGroqClient:
    """Mimics groq client: captures create() kwargs, returns a scripted reply."""

    def __init__(self, reply=None, error=None):
        self._reply = reply
        self._error = error
        self.captured = None
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs):
        self.captured = kwargs
        if self._error is not None:
            raise self._error
        return self._reply


def _groq_reply(content=None, tool_calls=None, usage=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice], usage=usage)


def test_groq_request_conversion():
    client = FakeGroqClient(reply=_groq_reply(content="hi"))
    provider = GroqProvider(model="test-model", client=client)
    schemas = [{"name": "get_tasks", "parameters": {"type": "object"}}]

    provider.generate([{"role": "user", "content": "hi"}], schemas)

    assert client.captured["model"] == "test-model"
    # Tools wrapped in OpenAI function envelope.
    assert client.captured["tools"] == [
        {"type": "function", "function": schemas[0]}
    ]
    assert client.captured["tool_choice"] == "auto"


def test_groq_no_tools_sets_tool_choice_none():
    client = FakeGroqClient(reply=_groq_reply(content="hi"))
    GroqProvider(client=client).generate([{"role": "user", "content": "hi"}], None)
    assert client.captured["tools"] is None
    assert client.captured["tool_choice"] == "none"


def test_groq_tool_call_parsing():
    tc = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="create_task", arguments='{"title": "x"}'),
    )
    client = FakeGroqClient(reply=_groq_reply(tool_calls=[tc]))
    resp = GroqProvider(client=client).generate([{"role": "user", "content": "go"}], [])

    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "create_task"
    assert resp.tool_calls[0].arguments == {"title": "x"}


def test_groq_malformed_tool_arguments_become_none():
    tc = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="create_task", arguments="{not json"),
    )
    client = FakeGroqClient(reply=_groq_reply(tool_calls=[tc]))
    resp = GroqProvider(client=client).generate([{"role": "user", "content": "go"}], [])
    assert resp.tool_calls[0].arguments is None


def test_groq_response_and_usage_parsing():
    usage = SimpleNamespace(
        prompt_tokens=10, completion_tokens=5, total_tokens=15
    )
    client = FakeGroqClient(reply=_groq_reply(content="answer", usage=usage))
    resp = GroqProvider(model="m", client=client).generate(
        [{"role": "user", "content": "q"}], None
    )

    assert resp.text == "answer"
    assert resp.usage == Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    assert resp.latency_ms is not None
    assert resp.model == "m"


def test_groq_malformed_response_is_mapped():
    # choices missing -> parsing raises -> MALFORMED_RESPONSE
    bad = SimpleNamespace(choices=[], usage=None)
    client = FakeGroqClient(reply=bad)
    with pytest.raises(ProviderError) as exc:
        GroqProvider(client=client).generate([{"role": "user", "content": "q"}], None)
    assert exc.value.code == ERR_MALFORMED_RESPONSE


# --- Groq: error mapping ---------------------------------------------------

@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (type("AuthenticationError", (Exception,), {})(), ERR_INVALID_API_KEY),
        (type("RateLimitError", (Exception,), {})(), ERR_RATE_LIMIT),
        (type("APITimeoutError", (Exception,), {})(), ERR_TIMEOUT),
        (type("NotFoundError", (Exception,), {})(), ERR_MODEL_UNAVAILABLE),
        (type("APIConnectionError", (Exception,), {})(), ERR_PROVIDER_UNAVAILABLE),
    ],
)
def test_groq_error_mapping_by_name(exc, code):
    client = FakeGroqClient(error=exc)
    with pytest.raises(ProviderError) as raised:
        GroqProvider(client=client).generate([{"role": "user", "content": "q"}], None)
    assert raised.value.code == code


def test_groq_error_mapping_by_status_code():
    err = type("SomeApiError", (Exception,), {})()
    err.status_code = 429
    client = FakeGroqClient(error=err)
    with pytest.raises(ProviderError) as raised:
        GroqProvider(client=client).generate([{"role": "user", "content": "q"}], None)
    assert raised.value.code == ERR_RATE_LIMIT


# --- Gemini: request conversion (pure function) ----------------------------

def test_gemini_message_conversion():
    messages = [
        {"role": "system", "content": "be helpful"},
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "get_tasks", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "c1",
            "name": "get_tasks",
            "content": json.dumps({"success": True, "data": {"tasks": []}}),
        },
    ]
    system, contents = GeminiProvider._to_contents(messages)

    assert system == "be helpful"
    assert contents[0] == {"role": "user", "parts": ["hi"]}
    # assistant tool call -> model role with a function_call part
    assert contents[1]["role"] == "model"
    assert contents[1]["parts"][0]["function_call"]["name"] == "get_tasks"
    # tool result -> user role with a function_response part
    assert contents[2]["role"] == "user"
    assert contents[2]["parts"][0]["function_response"]["name"] == "get_tasks"


# --- Gemini: response parsing ----------------------------------------------

class FakeGeminiModel:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.captured = None

    def generate_content(self, contents):
        self.captured = contents
        if self._error is not None:
            raise self._error
        return self._response


def _gemini_response(parts, usage=None):
    content = SimpleNamespace(parts=parts)
    candidate = SimpleNamespace(content=content)
    return SimpleNamespace(candidates=[candidate], usage_metadata=usage)


def test_gemini_tool_call_and_text_parsing():
    fc_part = SimpleNamespace(
        function_call=SimpleNamespace(name="create_task", args={"title": "y"}),
        text=None,
    )
    usage = SimpleNamespace(
        prompt_token_count=7, candidates_token_count=3, total_token_count=10
    )
    model = FakeGeminiModel(response=_gemini_response([fc_part], usage=usage))
    resp = GeminiProvider(model="gm", client=model).generate(
        [{"role": "user", "content": "add y"}], [{"name": "create_task"}]
    )

    assert resp.tool_calls[0].name == "create_task"
    assert resp.tool_calls[0].arguments == {"title": "y"}
    assert resp.usage.total_tokens == 10
    assert resp.model == "gm"


def test_gemini_sanitizes_unsupported_schema_keys():
    # Gemini's Schema proto rejects keys like additionalProperties/pattern/minimum
    # that the canonical OpenAI-style schemas carry. The adapter must strip them
    # recursively without mutating the input.
    canonical = [
        {
            "name": "create_task",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "minLength": 1},
                    "due_date": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
                    "minutes": {"type": "integer", "minimum": 0, "maximum": 999},
                },
                "required": ["title"],
                "additionalProperties": False,
            },
        }
    ]
    cleaned = GeminiProvider._sanitize_schema(canonical)
    params = cleaned[0]["parameters"]
    assert "additionalProperties" not in params
    assert "pattern" not in params["properties"]["due_date"]
    assert "minimum" not in params["properties"]["minutes"]
    assert "minLength" not in params["properties"]["title"]
    # Supported structure is preserved.
    assert params["properties"]["title"]["type"] == "string"
    assert params["required"] == ["title"]
    # Input is not mutated.
    assert "additionalProperties" in canonical[0]["parameters"]


def test_gemini_text_only_response():
    text_part = SimpleNamespace(function_call=None, text="plain answer")
    model = FakeGeminiModel(response=_gemini_response([text_part]))
    resp = GeminiProvider(client=model).generate(
        [{"role": "user", "content": "q"}], None
    )
    assert resp.text == "plain answer"
    assert resp.tool_calls == []


def test_gemini_malformed_response_is_mapped():
    model = FakeGeminiModel(response=SimpleNamespace(candidates=[]))
    with pytest.raises(ProviderError) as exc:
        GeminiProvider(client=model).generate([{"role": "user", "content": "q"}], None)
    assert exc.value.code == ERR_MALFORMED_RESPONSE


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (type("Unauthenticated", (Exception,), {})(), ERR_INVALID_API_KEY),
        (type("ResourceExhausted", (Exception,), {})(), ERR_RATE_LIMIT),
        (type("DeadlineExceeded", (Exception,), {})(), ERR_TIMEOUT),
        (type("NotFound", (Exception,), {})(), ERR_MODEL_UNAVAILABLE),
        (type("ServiceUnavailable", (Exception,), {})(), ERR_PROVIDER_UNAVAILABLE),
    ],
)
def test_gemini_error_mapping_by_name(exc, code):
    model = FakeGeminiModel(error=exc)
    with pytest.raises(ProviderError) as raised:
        GeminiProvider(client=model).generate([{"role": "user", "content": "q"}], None)
    assert raised.value.code == code


# --- Ollama: local provider -------------------------------------------------

class FakeOllamaClient:
    """Mimics ollama.Client: captures chat() kwargs, returns a scripted reply."""

    def __init__(self, reply=None, error=None):
        self._reply = reply
        self._error = error
        self.captured = None

    def chat(self, **kwargs):
        self.captured = kwargs
        if self._error is not None:
            raise self._error
        return self._reply


def _ollama_reply(content=None, tool_calls=None, prompt=None, completion=None):
    fn_calls = []
    for name, args in tool_calls or []:
        fn_calls.append(SimpleNamespace(function=SimpleNamespace(name=name, arguments=args)))
    message = SimpleNamespace(content=content, tool_calls=fn_calls or None)
    return SimpleNamespace(
        message=message, prompt_eval_count=prompt, eval_count=completion
    )


def test_ollama_tool_call_and_usage_parsing():
    client = FakeOllamaClient(
        reply=_ollama_reply(
            tool_calls=[("create_task", {"title": "x"})], prompt=12, completion=4
        )
    )
    resp = OllamaProvider(model="ayarlicazhocam:latest", client=client).generate(
        [{"role": "user", "content": "add x"}], [{"name": "create_task"}]
    )
    assert resp.tool_calls[0].name == "create_task"
    assert resp.tool_calls[0].arguments == {"title": "x"}
    assert resp.usage == Usage(prompt_tokens=12, completion_tokens=4, total_tokens=16)
    assert resp.model == "ayarlicazhocam:latest"
    # Tools are wrapped in the OpenAI function envelope for Ollama.
    assert client.captured["tools"] == [
        {"type": "function", "function": {"name": "create_task"}}
    ]


def test_ollama_text_only_response():
    client = FakeOllamaClient(reply=_ollama_reply(content="plain answer"))
    resp = OllamaProvider(client=client).generate(
        [{"role": "user", "content": "q"}], None
    )
    assert resp.text == "plain answer"
    assert resp.tool_calls == []
    assert client.captured["tools"] is None


def test_ollama_message_conversion_reparses_tool_arguments():
    # Assistant tool_calls carry JSON-string arguments; Ollama needs objects.
    messages = [
        {"role": "system", "content": "be helpful"},
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "get_tasks", "arguments": '{"overdue": true}'}}
            ],
        },
        {"role": "tool", "name": "get_tasks", "content": '{"count": 0}'},
    ]
    converted = OllamaProvider._to_ollama(messages)
    assert converted[0] == {"role": "system", "content": "be helpful"}
    assert converted[2]["tool_calls"][0]["function"]["arguments"] == {"overdue": True}
    assert converted[3] == {"role": "tool", "content": '{"count": 0}', "tool_name": "get_tasks"}


def test_ollama_connection_error_is_provider_unavailable():
    err = type("ConnectError", (Exception,), {})()
    client = FakeOllamaClient(error=err)
    with pytest.raises(ProviderError) as raised:
        OllamaProvider(client=client).generate([{"role": "user", "content": "q"}], None)
    assert raised.value.code == ERR_PROVIDER_UNAVAILABLE


def test_ollama_recovers_malformed_text_tool_call():
    # Some fine-tunes emit the call as (malformed) text instead of a native
    # tool_calls field. The provider must recover it so the tool loop runs.
    bad = (
        '{"name":"create_task","parameters={"priority": "high", '
        '"title": "Finish README"}}'
    )
    client = FakeOllamaClient(reply=_ollama_reply(content=bad))
    resp = OllamaProvider(client=client).generate(
        [{"role": "user", "content": "add it"}], [{"name": "create_task"}]
    )
    assert [c.name for c in resp.tool_calls] == ["create_task"]
    assert resp.tool_calls[0].arguments == {"priority": "high", "title": "Finish README"}
    # The raw text is dropped: it was a tool request, not a final answer.
    assert resp.text is None


def test_ollama_recovers_tool_call_tag_form():
    text = 'Sure!<tool_call>{"name": "get_tasks", "arguments": {"status": "todo"}}</tool_call>'
    client = FakeOllamaClient(reply=_ollama_reply(content=text))
    resp = OllamaProvider(client=client).generate(
        [{"role": "user", "content": "list"}], [{"name": "get_tasks"}]
    )
    assert resp.tool_calls[0].name == "get_tasks"
    assert resp.tool_calls[0].arguments == {"status": "todo"}


def test_ollama_plain_text_is_not_mistaken_for_a_tool_call():
    client = FakeOllamaClient(reply=_ollama_reply(content="You have 6 tasks."))
    resp = OllamaProvider(client=client).generate(
        [{"role": "user", "content": "how many?"}], None
    )
    assert resp.tool_calls == []
    assert resp.text == "You have 6 tasks."


def test_ollama_native_tool_calls_take_precedence_over_text():
    # If native tool_calls exist, the text fallback must not run.
    client = FakeOllamaClient(
        reply=_ollama_reply(
            content='{"name": "get_tasks", "arguments": {}}',
            tool_calls=[("create_task", {"title": "x"})],
        )
    )
    resp = OllamaProvider(client=client).generate(
        [{"role": "user", "content": "go"}], [{"name": "create_task"}]
    )
    assert [c.name for c in resp.tool_calls] == ["create_task"]


def test_get_provider_selects_ollama(monkeypatch):
    class DummyOllama(P.Provider):
        name = "ollama"

        def __init__(self, **kwargs):
            pass

        def generate(self, messages, tools):
            return P.ProviderResponse(text="x")

    monkeypatch.setattr(P, "OllamaProvider", DummyOllama)
    monkeypatch.setenv("MODEL_PROVIDER", "ollama")
    assert get_provider().name == "ollama"
