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
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
DEFAULT_GEMINI_MODEL = "gemini-1.5-flash"
DEFAULT_OLLAMA_MODEL = "ayarlicazhocam:latest"

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

# Ollama runs locally. ``ResponseError`` is intentionally left out so that
# ``_map_sdk_error`` falls through to its ``status_code`` (e.g. 404 -> model
# unavailable). Only connection/timeout failures are mapped by class name.
_OLLAMA_ERROR_NAMES: dict[str, str] = {
    "ConnectError": ERR_PROVIDER_UNAVAILABLE,
    "ConnectionError": ERR_PROVIDER_UNAVAILABLE,
    "TimeoutException": ERR_TIMEOUT,
    "ReadTimeout": ERR_TIMEOUT,
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

    # JSON-Schema keywords the canonical (OpenAI-style) tool schemas use that
    # Gemini's ``Schema`` proto does not accept. Groq consumes the schemas as-is;
    # Gemini requires them stripped, so the translation lives here in the adapter
    # rather than polluting the provider-neutral schemas in ``tools/schemas.py``.
    _UNSUPPORTED_SCHEMA_KEYS: frozenset[str] = frozenset(
        {
            "additionalProperties",
            "pattern",
            "minimum",
            "maximum",
            "minItems",
            "maxItems",
            "minLength",
            "maxLength",
        }
    )

    @classmethod
    def _sanitize_schema(cls, obj: Any) -> Any:
        """Recursively drop keys Gemini's Schema proto rejects.

        Returns a new structure; the input (the shared canonical schema) is
        never mutated.
        """
        if isinstance(obj, dict):
            return {
                key: cls._sanitize_schema(value)
                for key, value in obj.items()
                if key not in cls._UNSUPPORTED_SCHEMA_KEYS
            }
        if isinstance(obj, list):
            return [cls._sanitize_schema(item) for item in obj]
        return obj

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
        gem_tools = (
            [{"function_declarations": self._sanitize_schema(tools)}]
            if tools
            else None
        )

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


class OllamaProvider(Provider):
    """Local Ollama provider (OpenAI-style tool calling, fully offline).

    Runs against a local Ollama server (default ``http://localhost:11434``), so
    there are no API keys and no rate limits. Uses the same canonical message
    and tool-schema format as the other providers; only the transport differs.
    """

    name = "ollama"

    def __init__(
        self,
        model: str | None = None,
        host: str | None = None,
        *,
        client: Any | None = None,
    ) -> None:
        self._model = model or os.environ.get("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL
        if client is not None:  # injected for tests
            self._client = client
            return
        try:
            import ollama
        except ImportError as exc:
            raise ProviderError(
                "The 'ollama' package is not installed.", code=ERR_CONFIG
            ) from exc
        host = host or os.environ.get("OLLAMA_HOST")
        self._client = ollama.Client(host=host) if host else ollama.Client()

    # -- text-form tool-call recovery ---------------------------------------
    # Some models (notably small fine-tunes) emit a tool call as TEXT in the
    # content instead of via the native tool_calls field — e.g.
    #   <tool_call>{"name": "create_task", "arguments": {...}}</tool_call>
    # or a bare {"name": "...", "parameters"/"arguments": {...}} object, often
    # slightly malformed (`"parameters=` instead of `"parameters":`). Without
    # recovery these are treated as a final answer and no tool ever runs. This
    # parser rescues those calls so the tool loop still works.

    @staticmethod
    def _json_like_objects(text: str) -> list[str]:
        """Return balanced-brace ``{...}`` substrings (handles nesting)."""
        objects: list[str] = []
        depth = 0
        start: int | None = None
        for i, ch in enumerate(text):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}" and depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    objects.append(text[start : i + 1])
                    start = None
        return objects

    @staticmethod
    def _tolerant_json(fragment: str) -> Any:
        """Parse JSON, tolerating the common ``"key=`` fine-tune malformation."""
        candidates = [
            fragment,
            fragment.replace('parameters=', 'parameters":').replace(
                'arguments=', 'arguments":'
            ),
        ]
        for candidate in candidates:
            try:
                return json.loads(candidate)
            except (json.JSONDecodeError, TypeError):
                continue
        return None

    @classmethod
    def _extract_text_tool_calls(cls, text: str | None) -> list[ToolCall]:
        """Recover tool calls a model emitted as text. Empty list if none."""
        if not text:
            return []
        # Prefer explicit <tool_call>...</tool_call> blocks, else scan for any
        # JSON-like object that names a function.
        blocks = re.findall(
            r"<tool_call>\s*(\{.*?\})\s*</tool_call>", text, re.DOTALL
        )
        fragments = blocks or [
            frag for frag in cls._json_like_objects(text) if '"name"' in frag
        ]
        calls: list[ToolCall] = []
        for i, fragment in enumerate(fragments):
            obj = cls._tolerant_json(fragment)
            if not isinstance(obj, dict):
                continue
            name = obj.get("name")
            if not isinstance(name, str) or not name:
                continue
            args = obj.get("arguments")
            if not isinstance(args, dict):
                args = obj.get("parameters")
            if not isinstance(args, dict):
                args = {}
            calls.append(ToolCall(id=f"ollama_text_{name}_{i}", name=name, arguments=args))
        return calls

    @staticmethod
    def _to_ollama(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert canonical OpenAI-style messages into Ollama chat messages.

        Ollama expects tool-call ``arguments`` as an object (not a JSON string),
        so assistant tool calls are re-parsed on the way through.
        """
        out: list[dict[str, Any]] = []
        for message in messages:
            role = message.get("role")
            if role in ("system", "user"):
                out.append({"role": role, "content": message.get("content", "") or ""})
            elif role == "assistant":
                entry: dict[str, Any] = {
                    "role": "assistant",
                    "content": message.get("content", "") or "",
                }
                tool_calls: list[dict[str, Any]] = []
                for tc in message.get("tool_calls", []) or []:
                    fn = tc.get("function", tc)
                    raw = fn.get("arguments")
                    args = json.loads(raw) if isinstance(raw, str) else (raw or {})
                    tool_calls.append(
                        {"function": {"name": fn.get("name"), "arguments": args}}
                    )
                if tool_calls:
                    entry["tool_calls"] = tool_calls
                out.append(entry)
            elif role == "tool":
                entry = {"role": "tool", "content": message.get("content", "") or ""}
                if message.get("name"):
                    entry["tool_name"] = message["name"]
                out.append(entry)
        return out

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
            response = self._client.chat(
                model=self._model,
                messages=self._to_ollama(messages),
                tools=tool_param,
            )
        except Exception as exc:
            raise _map_sdk_error(exc, _OLLAMA_ERROR_NAMES, "Ollama") from exc
        latency_ms = (time.perf_counter() - start) * 1000

        try:
            message = response.message
            calls: list[ToolCall] = []
            for i, tc in enumerate(getattr(message, "tool_calls", None) or []):
                fn = tc.function
                raw = fn.arguments
                arguments: Any = dict(raw) if isinstance(raw, dict) else raw
                calls.append(
                    ToolCall(id=f"ollama_{fn.name}_{i}", name=fn.name, arguments=arguments)
                )
            usage = self._parse_usage(response)
            text = getattr(message, "content", None) or None
        except (AttributeError, TypeError) as exc:
            raise ProviderError(
                f"Malformed Ollama response: {exc}", code=ERR_MALFORMED_RESPONSE
            ) from exc

        # Fallback: if the model produced no native tool calls but wrote one as
        # text, recover it so the tool loop still runs. The recovered call is a
        # tool request, not a final answer, so the raw text is dropped.
        if not calls and text:
            recovered = self._extract_text_tool_calls(text)
            if recovered:
                calls = recovered
                text = None

        return ProviderResponse(
            text=text,
            tool_calls=calls,
            usage=usage,
            latency_ms=latency_ms,
            model=self._model,
        )

    @staticmethod
    def _parse_usage(response: Any) -> Usage | None:
        prompt = getattr(response, "prompt_eval_count", None)
        completion = getattr(response, "eval_count", None)
        if prompt is None and completion is None:
            return None
        total = (prompt or 0) + (completion or 0) if (prompt or completion) else None
        return Usage(
            prompt_tokens=prompt, completion_tokens=completion, total_tokens=total
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
    if provider_name in ("ollama", "local"):
        return OllamaProvider(**kwargs)
    raise ProviderError(
        f"Unknown MODEL_PROVIDER: {provider_name!r}.", code=ERR_CONFIG
    )
