"""Model-provider abstraction and concrete Groq / Gemini implementations.

The rest of the agent depends only on the :class:`Provider` interface and the
normalized :class:`ProviderResponse` / :class:`ToolCall` / :class:`Usage` data
— never on a specific SDK. Switching providers is done by changing
``MODEL_PROVIDER`` in ``.env``; no other code changes.

SDKs are imported lazily inside ``__init__`` so importing this module never
requires ``groq`` or ``google-generativeai`` to be installed. Every provider
translates the canonical OpenAI-style message list to/from its own format and
maps SDK errors onto :class:`ProviderError` (with a stable ``code``) so the
orchestrator never sees an SDK-specific exception and never crashes.
"""

from __future__ import annotations

import json
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
DEFAULT_GEMINI_MODEL = "gemini-1.5-flash"

# --- Stable error codes (independent of any SDK) ---------------------------
ERR_CONFIG = "CONFIG_ERROR"
ERR_INVALID_API_KEY = "INVALID_API_KEY"
ERR_RATE_LIMIT = "RATE_LIMIT"
ERR_TIMEOUT = "TIMEOUT"
ERR_MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
ERR_PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
ERR_MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
ERR_REQUEST_FAILED = "PROVIDER_REQUEST_FAILED"


class ProviderError(Exception):
    """Raised when a provider is misconfigured, unavailable, or a call fails.

    ``code`` is one of the ``ERR_*`` constants above. The orchestrator catches
    this and returns a graceful error result instead of crashing.
    """

    def __init__(self, message: str, code: str = ERR_REQUEST_FAILED) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Usage:
    """Token usage for one request; any field may be ``None`` if unreported."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass
class ToolCall:
    """A normalized tool-call request emitted by a model.

    ``arguments`` is expected to be a ``dict``; providers set it to something
    else (or ``None``) when the model returned malformed JSON, and the
    orchestrator turns that into a ``MALFORMED_ARGUMENTS`` result.
    """

    id: str
    name: str
    arguments: Any

    def to_message(self) -> dict[str, Any]:
        """Serialize back into an OpenAI-style assistant tool_call entry."""
        args = self.arguments
        arguments_str = args if isinstance(args, str) else json.dumps(args)
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": arguments_str},
        }


@dataclass
class ProviderResponse:
    """Normalized model output plus optional telemetry."""

    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage | None = None
    latency_ms: float | None = None
    model: str | None = None


# --- Error-mapping tables --------------------------------------------------

_STATUS_CODE_MAP: dict[int, str] = {
    400: ERR_REQUEST_FAILED,
    401: ERR_INVALID_API_KEY,
    403: ERR_INVALID_API_KEY,
    404: ERR_MODEL_UNAVAILABLE,
    408: ERR_TIMEOUT,
    429: ERR_RATE_LIMIT,
}

_GROQ_ERROR_NAMES: dict[str, str] = {
    "AuthenticationError": ERR_INVALID_API_KEY,
    "PermissionDeniedError": ERR_INVALID_API_KEY,
    "RateLimitError": ERR_RATE_LIMIT,
    "APITimeoutError": ERR_TIMEOUT,
    "APIConnectionError": ERR_PROVIDER_UNAVAILABLE,
    "InternalServerError": ERR_PROVIDER_UNAVAILABLE,
    "NotFoundError": ERR_MODEL_UNAVAILABLE,
}

_GEMINI_ERROR_NAMES: dict[str, str] = {
    "Unauthenticated": ERR_INVALID_API_KEY,
    "PermissionDenied": ERR_INVALID_API_KEY,
    "ResourceExhausted": ERR_RATE_LIMIT,
    "DeadlineExceeded": ERR_TIMEOUT,
    "NotFound": ERR_MODEL_UNAVAILABLE,
    "ServiceUnavailable": ERR_PROVIDER_UNAVAILABLE,
    "InternalServerError": ERR_PROVIDER_UNAVAILABLE,
    "GoogleAPICallError": ERR_PROVIDER_UNAVAILABLE,
}


def _map_sdk_error(exc: Exception, name_map: dict[str, str], label: str) -> ProviderError:
    """Translate an arbitrary SDK exception into a coded :class:`ProviderError`.

    Resolution order: exception class name → HTTP ``status_code`` → generic.
    """
    code = name_map.get(type(exc).__name__)
    if code is None:
        status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        if isinstance(status, int):
            code = _STATUS_CODE_MAP.get(status)
            if code is None and status >= 500:
                code = ERR_PROVIDER_UNAVAILABLE
    if code is None:
        code = ERR_REQUEST_FAILED
    return ProviderError(f"{label} request failed: {exc}", code=code)


class Provider(ABC):
    """Common interface every provider implements."""

    name: str = "provider"

    @abstractmethod
    def generate(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
    ) -> ProviderResponse:
        """Send the conversation (+ tool schemas) and return a normalized reply.

        Implementations must raise :class:`ProviderError` (not SDK-specific
        exceptions) on failure.
        """
        raise NotImplementedError


class GroqProvider(Provider):
    """Groq provider (OpenAI-compatible chat completions + tool calling)."""

    name = "groq"

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        *,
        client: Any | None = None,
    ) -> None:
        self._model = model or os.environ.get("GROQ_MODEL") or DEFAULT_GROQ_MODEL
        if client is not None:  # injected for tests
            self._client = client
            return
        try:
            from groq import Groq
        except ImportError as exc:
            raise ProviderError(
                "The 'groq' package is not installed.", code=ERR_CONFIG
            ) from exc
        key = api_key or os.environ.get("GROQ_API_KEY")
        if not key:
            raise ProviderError("GROQ_API_KEY is not set.", code=ERR_CONFIG)
        self._client = Groq(api_key=key)

    def generate(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
    ) -> ProviderResponse:
        tool_param = (
            [{"type": "function", "function": schema} for schema in tools]
            if tools
            else None
        )
        start = time.perf_counter()
        try:
            completion = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=tool_param,
                tool_choice="auto" if tool_param else "none",
            )
        except Exception as exc:
            raise _map_sdk_error(exc, _GROQ_ERROR_NAMES, "Groq") from exc
        latency_ms = (time.perf_counter() - start) * 1000

        try:
            message = completion.choices[0].message
            calls: list[ToolCall] = []
            for tc in getattr(message, "tool_calls", None) or []:
                try:
                    arguments: Any = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    arguments = None  # malformed; orchestrator will flag it
                calls.append(
                    ToolCall(id=tc.id, name=tc.function.name, arguments=arguments)
                )
            usage = self._parse_usage(getattr(completion, "usage", None))
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(
                f"Malformed Groq response: {exc}", code=ERR_MALFORMED_RESPONSE
            ) from exc

        return ProviderResponse(
            text=message.content,
            tool_calls=calls,
            usage=usage,
            latency_ms=latency_ms,
            model=self._model,
        )

    @staticmethod
    def _parse_usage(raw: Any) -> Usage | None:
        if raw is None:
            return None
        return Usage(
            prompt_tokens=getattr(raw, "prompt_tokens", None),
            completion_tokens=getattr(raw, "completion_tokens", None),
            total_tokens=getattr(raw, "total_tokens", None),
        )


class GeminiProvider(Provider):
    """Google Gemini provider (function calling via google-generativeai).

    Translates the canonical OpenAI-style message list into Gemini ``contents``
    and normalizes the response back into :class:`ProviderResponse`.
    """

    name = "gemini"

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        *,
        client: Any | None = None,
    ) -> None:
        self._model_name = model or os.environ.get("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL
        self._client = client  # injected generative model (tests) or None
        if client is not None:
            self._genai = None
            return
        try:
            import google.generativeai as genai
        except ImportError as exc:
            raise ProviderError(
                "The 'google-generativeai' package is not installed.",
                code=ERR_CONFIG,
            ) from exc
        key = (
            api_key
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
        )
        if not key:
            raise ProviderError(
                "GEMINI_API_KEY (or GOOGLE_API_KEY) is not set.", code=ERR_CONFIG
            )
        genai.configure(api_key=key)
        self._genai = genai

    @staticmethod
    def _to_contents(
        messages: list[dict[str, Any]],
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """Convert canonical messages -> (system_instruction, Gemini contents)."""
        system_instruction: str | None = None
        contents: list[dict[str, Any]] = []
        for message in messages:
            role = message.get("role")
            if role == "system":
                system_instruction = message.get("content")
            elif role == "user":
                contents.append({"role": "user", "parts": [message.get("content", "")]})
            elif role == "assistant":
                parts: list[Any] = []
                if message.get("content"):
                    parts.append(message["content"])
                for tc in message.get("tool_calls", []):
                    fn = tc["function"]
                    raw = fn.get("arguments")
                    args = json.loads(raw) if isinstance(raw, str) else (raw or {})
                    parts.append({"function_call": {"name": fn["name"], "args": args}})
                contents.append({"role": "model", "parts": parts})
            elif role == "tool":
                try:
                    response = json.loads(message.get("content", "{}"))
                except json.JSONDecodeError:
                    response = {"result": message.get("content", "")}
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "function_response": {
                                    "name": message.get("name", ""),
                                    "response": response,
                                }
                            }
                        ],
                    }
                )
        return system_instruction, contents

    def generate(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
    ) -> ProviderResponse:
        system_instruction, contents = self._to_contents(messages)
        gem_tools = [{"function_declarations": tools}] if tools else None

        start = time.perf_counter()
        try:
            if self._client is not None:
                model = self._client  # injected (tests)
            else:
                model = self._genai.GenerativeModel(
                    self._model_name,
                    tools=gem_tools,
                    system_instruction=system_instruction,
                )
            response = model.generate_content(contents)
        except Exception as exc:
            raise _map_sdk_error(exc, _GEMINI_ERROR_NAMES, "Gemini") from exc
        latency_ms = (time.perf_counter() - start) * 1000

        try:
            calls: list[ToolCall] = []
            text_parts: list[str] = []
            parts = response.candidates[0].content.parts
            for part in parts:
                fc = getattr(part, "function_call", None)
                if fc:
                    calls.append(
                        ToolCall(
                            id=f"gemini_{fc.name}",
                            name=fc.name,
                            arguments=dict(fc.args) if fc.args else {},
                        )
                    )
                elif getattr(part, "text", None):
                    text_parts.append(part.text)
            usage = self._parse_usage(getattr(response, "usage_metadata", None))
        except (AttributeError, IndexError, TypeError) as exc:
            raise ProviderError(
                f"Malformed Gemini response: {exc}", code=ERR_MALFORMED_RESPONSE
            ) from exc

        return ProviderResponse(
            text="".join(text_parts) or None,
            tool_calls=calls,
            usage=usage,
            latency_ms=latency_ms,
            model=self._model_name,
        )

    @staticmethod
    def _parse_usage(raw: Any) -> Usage | None:
        if raw is None:
            return None
        return Usage(
            prompt_tokens=getattr(raw, "prompt_token_count", None),
            completion_tokens=getattr(raw, "candidates_token_count", None),
            total_tokens=getattr(raw, "total_token_count", None),
        )


def get_provider(name: str | None = None, **kwargs: Any) -> Provider:
    """Instantiate the configured provider.

    ``name`` defaults to ``MODEL_PROVIDER`` from the environment, then to
    ``"groq"``. Changing that env var is the only thing needed to switch
    providers. Raises :class:`ProviderError` (code ``CONFIG_ERROR``) on unknown
    or missing configuration.
    """
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:  # pragma: no cover - dotenv is a declared dependency
        pass

    provider_name = (name or os.environ.get("MODEL_PROVIDER") or "groq").strip().lower()
    if provider_name == "groq":
        return GroqProvider(**kwargs)
    if provider_name in ("gemini", "google"):
        return GeminiProvider(**kwargs)
    raise ProviderError(
        f"Unknown MODEL_PROVIDER: {provider_name!r}.", code=ERR_CONFIG
    )
