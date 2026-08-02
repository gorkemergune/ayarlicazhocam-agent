"""Agent orchestration: the tool-calling loop.

The orchestrator connects a :class:`~agent.providers.Provider` to the existing
tool layer. It only orchestrates — it contains no business logic and no
validation (those live in the service layer, reached via the tools).

Flow::

    user -> LLM -> tool request? -> execute tool -> append result -> LLM -> ...
    ... until the model returns no tool calls (final answer) or the depth
    limit is hit.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from tools import (
    TOOL_SCHEMAS,
    create_task_tool,
    get_tasks_tool,
    update_task_tool,
)

from .prompts import SYSTEM_PROMPT
from .providers import Provider, ProviderError, ProviderResponse, ToolCall, Usage

# A tool is any callable returning the standard envelope dict.
Tool = Callable[..., dict[str, Any]]

# Single source of tool dispatch — names must match the schema names.
DEFAULT_TOOL_REGISTRY: dict[str, Tool] = {
    "create_task": create_task_tool,
    "get_tasks": get_tasks_tool,
    "update_task": update_task_tool,
}

MAX_TOOL_DEPTH = 5


# --- Lightweight execution logging -----------------------------------------


@dataclass
class ToolExecution:
    """One tool invocation within a run."""

    order: int
    name: str
    elapsed_ms: float
    success: bool


@dataclass
class RequestMetric:
    """Telemetry for one provider request (Phase 6 addition)."""

    order: int
    latency_ms: float | None
    total_tokens: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
    model: str | None


@dataclass
class ExecutionLog:
    """Per-run log: provider, tools executed (in order), and elapsed time.

    Phase 6 extended this with per-request provider telemetry (latency, request
    count, and token usage when the provider reports it).
    """

    provider: str
    tools: list[ToolExecution] = field(default_factory=list)
    requests: list[RequestMetric] = field(default_factory=list)
    started_at: float = field(default_factory=time.perf_counter)
    total_ms: float | None = None

    def record(self, name: str, order: int, elapsed_ms: float, success: bool) -> None:
        self.tools.append(ToolExecution(order, name, elapsed_ms, success))

    def record_request(self, response: ProviderResponse) -> None:
        """Capture provider telemetry from one generate() call."""
        usage = response.usage or Usage()
        self.requests.append(
            RequestMetric(
                order=len(self.requests) + 1,
                latency_ms=response.latency_ms,
                total_tokens=usage.total_tokens,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                model=response.model,
            )
        )

    def finish(self) -> None:
        self.total_ms = (time.perf_counter() - self.started_at) * 1000

    def tool_names(self) -> list[str]:
        return [t.name for t in self.tools]

    @property
    def request_count(self) -> int:
        return len(self.requests)

    @property
    def tool_call_count(self) -> int:
        return len(self.tools)

    @property
    def total_tokens(self) -> int | None:
        """Summed token usage, or ``None`` if no request reported tokens."""
        reported = [r.total_tokens for r in self.requests if r.total_tokens is not None]
        return sum(reported) if reported else None

    @property
    def provider_latency_ms(self) -> float | None:
        """Summed provider latency, or ``None`` if none was measured."""
        measured = [r.latency_ms for r in self.requests if r.latency_ms is not None]
        return sum(measured) if measured else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "total_ms": self.total_ms,
            "request_count": self.request_count,
            "tool_call_count": self.tool_call_count,
            "total_tokens": self.total_tokens,
            "provider_latency_ms": self.provider_latency_ms,
            "requests": [
                {
                    "order": r.order,
                    "latency_ms": round(r.latency_ms, 2) if r.latency_ms else None,
                    "total_tokens": r.total_tokens,
                    "model": r.model,
                }
                for r in self.requests
            ],
            "tools": [
                {
                    "order": t.order,
                    "name": t.name,
                    "elapsed_ms": round(t.elapsed_ms, 2),
                    "success": t.success,
                }
                for t in self.tools
            ],
        }


@dataclass
class AgentResult:
    """The outcome of a single ``run`` call."""

    success: bool
    final_message: str
    provider: str
    tool_calls: list[str]
    log: ExecutionLog
    messages: list[dict[str, Any]]
    error: str | None = None


def _envelope(success: bool, data: Any, message: str, error: dict | None) -> dict:
    return {"success": success, "data": data, "message": message, "error": error}


def _orch_error(code: str, message: str) -> dict[str, Any]:
    """Build a standard failure envelope for orchestration-level errors."""
    return _envelope(False, None, message, {"code": code, "details": message})


class Orchestrator:
    """Drives the provider + tool loop for one assistant."""

    def __init__(
        self,
        provider: Provider,
        *,
        system_prompt: str = SYSTEM_PROMPT,
        registry: dict[str, Tool] | None = None,
        schemas: list[dict[str, Any]] | None = None,
        max_tool_depth: int = MAX_TOOL_DEPTH,
    ) -> None:
        self.provider = provider
        self.system_prompt = system_prompt
        self.registry = registry if registry is not None else DEFAULT_TOOL_REGISTRY
        self.schemas = schemas if schemas is not None else TOOL_SCHEMAS
        self.max_tool_depth = max_tool_depth

    # -- dispatch ------------------------------------------------------------

    def _dispatch(self, call: ToolCall, log: ExecutionLog, order: int) -> dict[str, Any]:
        """Execute one tool call, returning its envelope; never raises."""
        start = time.perf_counter()

        if call.name not in self.registry:
            envelope = _orch_error(
                "UNKNOWN_TOOL", f"Unknown tool: {call.name!r}."
            )
        elif not isinstance(call.arguments, dict):
            envelope = _orch_error(
                "MALFORMED_ARGUMENTS",
                f"Arguments for {call.name!r} must be a JSON object.",
            )
        else:
            try:
                envelope = self.registry[call.name](**call.arguments)
            except Exception:  # tool should not raise, but never crash the loop
                envelope = _orch_error(
                    "TOOL_EXECUTION_ERROR",
                    f"Tool {call.name!r} failed to execute.",
                )

        elapsed_ms = (time.perf_counter() - start) * 1000
        log.record(call.name, order, elapsed_ms, bool(envelope.get("success")))
        return envelope

    @staticmethod
    def _assistant_message(response: ProviderResponse) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": response.text or ""}
        if response.tool_calls:
            message["tool_calls"] = [tc.to_message() for tc in response.tool_calls]
        return message

    # -- main loop -----------------------------------------------------------

    def run(
        self, user_message: str, history: list[dict[str, Any]] | None = None
    ) -> AgentResult:
        """Run the tool loop for ``user_message`` and return the final result."""
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt}
        ]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        log = ExecutionLog(provider=self.provider.name)
        depth = 0
        order = 0

        while True:
            try:
                response = self.provider.generate(messages, self.schemas)
            except ProviderError:
                log.finish()
                return AgentResult(
                    success=False,
                    final_message="The model provider is currently unavailable.",
                    provider=self.provider.name,
                    tool_calls=log.tool_names(),
                    log=log,
                    messages=messages,
                    error="PROVIDER_ERROR",
                )

            log.record_request(response)  # capture provider telemetry (additive)

            if not response.tool_calls:
                log.finish()
                return AgentResult(
                    success=True,
                    final_message=response.text or "",
                    provider=self.provider.name,
                    tool_calls=log.tool_names(),
                    log=log,
                    messages=messages,
                    error=None,
                )

            if depth >= self.max_tool_depth:
                log.finish()
                return AgentResult(
                    success=False,
                    final_message=(
                        "Internal orchestration error: maximum tool-call depth "
                        "exceeded."
                    ),
                    provider=self.provider.name,
                    tool_calls=log.tool_names(),
                    log=log,
                    messages=messages,
                    error="MAX_TOOL_DEPTH_EXCEEDED",
                )

            depth += 1
            messages.append(self._assistant_message(response))
            for call in response.tool_calls:
                order += 1
                envelope = self._dispatch(call, log, order)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id or f"call_{order}",
                        "name": call.name,
                        # Feed the entire standardized envelope back, unmodified.
                        "content": json.dumps(envelope),
                    }
                )
