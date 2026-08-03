"""Agent layer: orchestration, prompts, and the model-provider abstraction.

This package connects an LLM to the tool layer. It only orchestrates: it holds
no business logic and no validation (those live in ``services``, reached via
``tools``). Switching providers is done via ``MODEL_PROVIDER`` in ``.env``.

Public API::

    from agent import (
        Orchestrator, AgentResult, ExecutionLog,
        get_provider, Provider, ProviderResponse, ToolCall, ProviderError,
        SYSTEM_PROMPT, DEFAULT_TOOL_REGISTRY, MAX_TOOL_DEPTH,
    )
"""

from __future__ import annotations

from .orchestrator import (
    DEFAULT_TOOL_REGISTRY,
    MAX_TOOL_DEPTH,
    AgentResult,
    ExecutionLog,
    Orchestrator,
    RequestMetric,
    ToolExecution,
)
from .prompts import SYSTEM_PROMPT
from .providers import (
    GeminiProvider,
    GroqProvider,
    OllamaProvider,
    Provider,
    ProviderError,
    ProviderResponse,
    ToolCall,
    Usage,
    get_provider,
)

__all__ = [
    "Orchestrator",
    "AgentResult",
    "ExecutionLog",
    "ToolExecution",
    "RequestMetric",
    "DEFAULT_TOOL_REGISTRY",
    "MAX_TOOL_DEPTH",
    "SYSTEM_PROMPT",
    "get_provider",
    "Provider",
    "ProviderResponse",
    "ToolCall",
    "Usage",
    "ProviderError",
    "GroqProvider",
    "GeminiProvider",
    "OllamaProvider",
]
