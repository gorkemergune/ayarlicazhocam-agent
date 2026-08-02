"""Tests for the tool layer (Phase 4).

These test the *adapter* behavior only — response envelope, argument
forwarding, and exception mapping — using a mocked ``TaskService``. Business
rules are already covered in ``test_task_service.py`` and are not retested here.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from services import (
    ProjectNotFoundError,
    ServiceDBError,
    TaskNotFoundError,
    TaskService,
    ValidationError,
)
from tools import (
    TOOL_SCHEMAS,
    create_task_tool,
    get_tasks_tool,
    update_task_tool,
)
from tools.schemas import (
    CREATE_TASK_SCHEMA,
    GET_TASKS_SCHEMA,
    UPDATE_TASK_SCHEMA,
)

SAMPLE_TASK = {"id": 1, "title": "demo", "status": "todo", "priority": "medium"}


@pytest.fixture()
def svc() -> MagicMock:
    """A mock standing in for TaskService (spec-checked)."""
    return MagicMock(spec=TaskService)


# --- create ----------------------------------------------------------------

def test_create_task_success(svc):
    svc.create_task.return_value = SAMPLE_TASK
    result = create_task_tool("demo", priority="high", service=svc)

    assert result["success"] is True
    assert result["data"] == SAMPLE_TASK
    assert result["error"] is None
    # Only provided optionals are forwarded; None values are dropped.
    svc.create_task.assert_called_once_with("demo", priority="high")


def test_create_task_drops_none_optionals(svc):
    svc.create_task.return_value = SAMPLE_TASK
    create_task_tool("demo", service=svc)
    svc.create_task.assert_called_once_with("demo")  # no None kwargs leak through


def test_create_task_validation_failure(svc):
    svc.create_task.side_effect = ValidationError("Title cannot be empty.")
    result = create_task_tool("   ", service=svc)

    assert result["success"] is False
    assert result["data"] is None
    assert result["error"]["code"] == "VALIDATION_ERROR"


# --- get_tasks -------------------------------------------------------------

def test_get_tasks_empty(svc):
    svc.get_tasks.return_value = []
    result = get_tasks_tool(service=svc)

    assert result["success"] is True
    assert result["data"] == {"tasks": [], "count": 0}
    svc.get_tasks.assert_called_once_with(
        status=None, priority=None, project_id=None, overdue=False
    )


def test_get_tasks_with_filters(svc):
    svc.get_tasks.return_value = [SAMPLE_TASK]
    result = get_tasks_tool(status="todo", priority="high", overdue=True, service=svc)

    assert result["success"] is True
    assert result["data"]["count"] == 1
    svc.get_tasks.assert_called_once_with(
        status="todo", priority="high", project_id=None, overdue=True
    )


# --- update ----------------------------------------------------------------

def test_update_task_success(svc):
    svc.update_task.return_value = {**SAMPLE_TASK, "status": "in_progress"}
    result = update_task_tool(1, status="in_progress", service=svc)

    assert result["success"] is True
    assert result["data"]["status"] == "in_progress"
    svc.update_task.assert_called_once_with(1, status="in_progress")


def test_update_nonexistent_task(svc):
    svc.update_task.side_effect = TaskNotFoundError("No task found with id 999.")
    result = update_task_tool(999, status="todo", service=svc)

    assert result["success"] is False
    assert result["error"]["code"] == "TASK_NOT_FOUND"


# --- exception mapping -----------------------------------------------------

@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (ValidationError("bad"), "VALIDATION_ERROR"),
        (TaskNotFoundError("missing"), "TASK_NOT_FOUND"),
        (ProjectNotFoundError("no project"), "PROJECT_NOT_FOUND"),
        (ServiceDBError("db down"), "DATABASE_ERROR"),
        (RuntimeError("boom"), "INTERNAL_ERROR"),  # non-domain -> generic
    ],
)
def test_exception_mapping(svc, exc, code):
    svc.create_task.side_effect = exc
    result = create_task_tool("x", service=svc)
    assert result["success"] is False
    assert result["error"]["code"] == code


def test_generic_exception_does_not_leak_details(svc):
    svc.create_task.side_effect = RuntimeError("secret internal detail")
    result = create_task_tool("x", service=svc)
    assert "secret internal detail" not in result["error"]["details"]
    assert "secret internal detail" not in result["message"]


# --- response envelope -----------------------------------------------------

def test_success_envelope_shape(svc):
    svc.get_tasks.return_value = []
    result = get_tasks_tool(service=svc)
    assert set(result.keys()) == {"success", "data", "message", "error"}
    assert result["error"] is None
    assert isinstance(result["message"], str)


def test_failure_envelope_shape(svc):
    svc.create_task.side_effect = ValidationError("bad")
    result = create_task_tool("x", service=svc)
    assert set(result.keys()) == {"success", "data", "message", "error"}
    assert result["data"] is None
    assert set(result["error"].keys()) == {"code", "details"}


# --- schemas ---------------------------------------------------------------

def test_schemas_are_valid_dicts():
    assert len(TOOL_SCHEMAS) == 3
    names = {s["name"] for s in TOOL_SCHEMAS}
    assert names == {"create_task", "get_tasks", "update_task"}

    for schema in TOOL_SCHEMAS:
        assert isinstance(schema, dict)
        assert isinstance(schema["name"], str)
        assert isinstance(schema["description"], str)
        params = schema["parameters"]
        assert params["type"] == "object"
        assert isinstance(params["properties"], dict)
        assert isinstance(params["required"], list)


def test_required_fields_declared():
    assert CREATE_TASK_SCHEMA["parameters"]["required"] == ["title"]
    assert UPDATE_TASK_SCHEMA["parameters"]["required"] == ["task_id"]
    assert GET_TASKS_SCHEMA["parameters"]["required"] == []
