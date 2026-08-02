"""Tool layer: the agent's only interface to stored state.

Tools are thin adapters over the service layer. Each returns the standardized
``{success, data, message, error}`` dict and never raises, never contains SQL,
and never duplicates the validation that lives in the services. A future model
provider is handed :data:`TOOL_SCHEMAS` and dispatches calls to the matching
``*_tool`` function.

Implemented in Phase 4: task tools (``create_task``, ``get_tasks``,
``update_task``). Planner/review tools come in later phases.
"""

from __future__ import annotations

from .schemas import (
    CREATE_TASK_SCHEMA,
    GET_TASKS_SCHEMA,
    TOOL_SCHEMAS,
    UPDATE_TASK_SCHEMA,
)
from .task_tools import create_task_tool, get_tasks_tool, update_task_tool

__all__ = [
    "create_task_tool",
    "get_tasks_tool",
    "update_task_tool",
    "TOOL_SCHEMAS",
    "CREATE_TASK_SCHEMA",
    "GET_TASKS_SCHEMA",
    "UPDATE_TASK_SCHEMA",
]
