"""Task tools — the thin adapter layer the LLM calls.

Each tool:

1. accepts function-call arguments,
2. calls a single :class:`~services.task_service.TaskService` method,
3. converts domain exceptions into the standardized tool response envelope.

There is **no business logic and no SQL here** — validation lives in the
service. Tools never raise; they always return the envelope so a model
provider gets a predictable, JSON-serializable result and never sees a Python
traceback.

Envelope (success)::

    {"success": True, "data": {...}, "message": "...", "error": None}

Envelope (failure)::

    {"success": False, "data": None, "message": "...",
     "error": {"code": "...", "details": "..."}}
"""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable, TypeVar

from services import (
    ProjectNotFoundError,
    ServiceDBError,
    ServiceError,
    TaskNotFoundError,
    TaskService,
    ValidationError,
)

# Default service instance. Constructing it opens no connection (the repository
# connects lazily), so this is safe at import time. Tests inject a mock via the
# ``service`` keyword argument on each tool.
_default_service = TaskService()

# Map domain exception types to stable, LLM-facing error codes. Internal
# exception class names are never exposed.
_ERROR_CODES: dict[type[Exception], str] = {
    ValidationError: "VALIDATION_ERROR",
    TaskNotFoundError: "TASK_NOT_FOUND",
    ProjectNotFoundError: "PROJECT_NOT_FOUND",
    ServiceDBError: "DATABASE_ERROR",
}

F = TypeVar("F", bound=Callable[..., dict[str, Any]])


def _success(data: Any, message: str) -> dict[str, Any]:
    return {"success": True, "data": data, "message": message, "error": None}


def _failure(code: str, message: str, details: str) -> dict[str, Any]:
    return {
        "success": False,
        "data": None,
        "message": message,
        "error": {"code": code, "details": details},
    }


def _code_for(exc: Exception) -> str:
    for exc_type, code in _ERROR_CODES.items():
        if isinstance(exc, exc_type):
            return code
    return "INTERNAL_ERROR"


def _tool(func: F) -> F:
    """Wrap a tool so any raised exception becomes an error envelope."""

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return func(*args, **kwargs)
        except ServiceError as exc:
            code = _code_for(exc)
            return _failure(code, str(exc), str(exc))
        except Exception:  # safety net — never leak a traceback to the model
            return _failure(
                "INTERNAL_ERROR",
                "An unexpected internal error occurred.",
                "The operation could not be completed.",
            )

    return wrapper  # type: ignore[return-value]


def _svc(service: TaskService | None) -> TaskService:
    return service if service is not None else _default_service


@_tool
def create_task_tool(
    title: str,
    description: str | None = None,
    project_id: int | None = None,
    priority: str | None = None,
    due_date: str | None = None,
    estimated_minutes: int | None = None,
    energy_level: str | None = None,
    *,
    service: TaskService | None = None,
) -> dict[str, Any]:
    """Create a task via ``TaskService.create_task``."""
    # Only forward provided values so the service applies its own defaults.
    optional: dict[str, Any] = {
        "description": description,
        "project_id": project_id,
        "priority": priority,
        "due_date": due_date,
        "estimated_minutes": estimated_minutes,
        "energy_level": energy_level,
    }
    kwargs = {k: v for k, v in optional.items() if v is not None}

    task = _svc(service).create_task(title, **kwargs)
    return _success(task, f"Created task #{task['id']}: {task['title']}.")


@_tool
def get_tasks_tool(
    status: str | None = None,
    priority: str | None = None,
    project_id: int | None = None,
    overdue: bool | None = None,
    *,
    service: TaskService | None = None,
) -> dict[str, Any]:
    """List tasks via ``TaskService.get_tasks`` with optional filters."""
    tasks = _svc(service).get_tasks(
        status=status,
        priority=priority,
        project_id=project_id,
        overdue=bool(overdue),
    )
    return _success(
        {"tasks": tasks, "count": len(tasks)},
        f"Found {len(tasks)} task(s).",
    )


@_tool
def update_task_tool(
    task_id: int,
    title: str | None = None,
    description: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    due_date: str | None = None,
    estimated_minutes: int | None = None,
    actual_minutes: int | None = None,
    energy_level: str | None = None,
    blocker: str | None = None,
    *,
    service: TaskService | None = None,
) -> dict[str, Any]:
    """Update a task via ``TaskService.update_task``.

    Only fields that are provided (non-null) are changed. If no updatable
    field is supplied, the service raises a validation error.
    """
    candidate: dict[str, Any] = {
        "title": title,
        "description": description,
        "status": status,
        "priority": priority,
        "due_date": due_date,
        "estimated_minutes": estimated_minutes,
        "actual_minutes": actual_minutes,
        "energy_level": energy_level,
        "blocker": blocker,
    }
    fields = {k: v for k, v in candidate.items() if v is not None}

    task = _svc(service).update_task(task_id, **fields)
    return _success(task, f"Updated task #{task['id']}.")
