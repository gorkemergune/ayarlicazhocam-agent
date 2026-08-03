"""JSON function-calling schemas for the task tools.

These are pure data (OpenAI / Gemma function-calling style) and are kept
independent from the tool implementations in ``task_tools.py`` so a model
provider can be handed the schemas without importing execution code.

Enum values are sourced from the service layer's allowed sets so the schemas
never drift from the business rules.
"""

from __future__ import annotations

from typing import Any

from services import ALLOWED_ENERGY_LEVELS, ALLOWED_PRIORITIES, ALLOWED_STATUSES

# Sorted lists give schemas a stable, deterministic order.
_STATUSES = sorted(ALLOWED_STATUSES)
_PRIORITIES = sorted(ALLOWED_PRIORITIES)
_ENERGY_LEVELS = sorted(ALLOWED_ENERGY_LEVELS)

_DATE_DESCRIPTION = "ISO date in YYYY-MM-DD format."
_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"

CREATE_TASK_SCHEMA: dict[str, Any] = {
    "name": "create_task",
    "description": (
        "Create a new task. Only call this when the user explicitly asks to "
        "add a task. Never invent a task. The project must already exist; "
        "projects are never created automatically."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Short, actionable task title. Required.",
            },
            "description": {
                "type": "string",
                "description": "Optional longer description of the task.",
            },
            "project_id": {
                "type": "integer",
                "description": (
                    "Optional. Id of an EXISTING project to attach the task to. "
                    "Omit this field entirely unless the user explicitly names a "
                    "project that already exists — never guess or invent a "
                    "project id."
                ),
            },
            "priority": {
                "type": "string",
                "enum": _PRIORITIES,
                "description": "Task priority. Defaults to 'medium' if omitted.",
            },
            "due_date": {
                "type": "string",
                "pattern": _DATE_PATTERN,
                "description": _DATE_DESCRIPTION,
            },
            "estimated_minutes": {
                "type": "integer",
                "minimum": 0,
                "description": "Estimated effort in minutes (>= 0).",
            },
            "energy_level": {
                "type": "string",
                "enum": _ENERGY_LEVELS,
                "description": "Energy the task requires.",
            },
        },
        "required": ["title"],
        "additionalProperties": False,
    },
}

GET_TASKS_SCHEMA: dict[str, Any] = {
    "name": "get_tasks",
    "description": (
        "List tasks from the database, optionally filtered. All filters are "
        "optional and combinable. Use this to read current task state; never "
        "answer about tasks from memory."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": _STATUSES,
                "description": "Filter by task status.",
            },
            "priority": {
                "type": "string",
                "enum": _PRIORITIES,
                "description": "Filter by priority.",
            },
            "project_id": {
                "type": "integer",
                "description": "Filter by project id.",
            },
            "overdue": {
                "type": "boolean",
                "description": (
                    "If true, return only actionable tasks whose due date has "
                    "passed (excludes completed and cancelled tasks)."
                ),
            },
        },
        "required": [],
        "additionalProperties": False,
    },
}

UPDATE_TASK_SCHEMA: dict[str, Any] = {
    "name": "update_task",
    "description": (
        "Update fields of an existing task. Only the provided fields are "
        "changed. Requires the task_id. Never report success unless the tool "
        "returns success."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "integer",
                "description": "Id of the task to update. Required.",
            },
            "title": {"type": "string", "description": "New task title."},
            "description": {
                "type": "string",
                "description": "New description.",
            },
            "status": {
                "type": "string",
                "enum": _STATUSES,
                "description": "New status.",
            },
            "priority": {
                "type": "string",
                "enum": _PRIORITIES,
                "description": "New priority.",
            },
            "due_date": {
                "type": "string",
                "pattern": _DATE_PATTERN,
                "description": _DATE_DESCRIPTION,
            },
            "estimated_minutes": {
                "type": "integer",
                "minimum": 0,
                "description": "New estimate in minutes (>= 0).",
            },
            "actual_minutes": {
                "type": "integer",
                "minimum": 0,
                "description": "Actual time spent in minutes (>= 0).",
            },
            "energy_level": {
                "type": "string",
                "enum": _ENERGY_LEVELS,
                "description": "New energy level.",
            },
            "blocker": {
                "type": "string",
                "description": "Description of what is blocking the task.",
            },
        },
        "required": ["task_id"],
        "additionalProperties": False,
    },
}

# The full tool set exposed to a model provider (planner tools come later).
TOOL_SCHEMAS: list[dict[str, Any]] = [
    CREATE_TASK_SCHEMA,
    GET_TASKS_SCHEMA,
    UPDATE_TASK_SCHEMA,
]
