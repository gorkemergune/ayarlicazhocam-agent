"""Tests for the domain service layer (Phase 3).

Each test runs against a fresh temp database with schema initialized and one
demo project inserted, so ``project_id`` references are valid.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from database import close_connection, execute, get_connection, initialize_database
from services import (
    ProjectNotFoundError,
    TaskNotFoundError,
    TaskService,
    ValidationError,
)


@pytest.fixture()
def service(tmp_path, monkeypatch):
    """Initialized temp DB + a demo project (id 1); yields a TaskService."""
    db_file = tmp_path / "svc.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    close_connection()
    conn = get_connection()
    initialize_database(conn)
    execute("INSERT INTO projects (name) VALUES (?);", ("Demo",))  # id = 1
    yield TaskService()
    close_connection()


def _iso(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


# --- create ----------------------------------------------------------------

def test_create_valid_task(service):
    task = service.create_task("Write report", priority="high", project_id=1)
    assert task["id"] > 0
    assert task["title"] == "Write report"
    assert task["priority"] == "high"
    assert task["status"] == "todo"  # default
    assert task["project_id"] == 1


def test_create_trims_title(service):
    task = service.create_task("   spaced   ")
    assert task["title"] == "spaced"


def test_reject_empty_title(service):
    with pytest.raises(ValidationError):
        service.create_task("   ")


def test_reject_invalid_priority(service):
    with pytest.raises(ValidationError):
        service.create_task("t", priority="urgent")


def test_reject_invalid_status(service):
    with pytest.raises(ValidationError):
        service.create_task("t", status="done")


def test_reject_invalid_energy_level(service):
    with pytest.raises(ValidationError):
        service.create_task("t", energy_level="extreme")


def test_reject_invalid_date(service):
    with pytest.raises(ValidationError):
        service.create_task("t", due_date="2026-13-40")
    with pytest.raises(ValidationError):
        service.create_task("t", due_date="08/02/2026")


def test_reject_negative_minutes(service):
    with pytest.raises(ValidationError):
        service.create_task("t", estimated_minutes=-5)


def test_reject_bool_minutes(service):
    with pytest.raises(ValidationError):
        service.create_task("t", estimated_minutes=True)


def test_coerce_string_and_float_minutes(service):
    # LLMs often send numbers as strings or floats; these are coerced to int.
    t1 = service.create_task("a", estimated_minutes="30")
    assert t1["estimated_minutes"] == 30
    t2 = service.create_task("b", estimated_minutes=45.0)
    assert t2["estimated_minutes"] == 45


def test_reject_non_numeric_and_fractional_minutes(service):
    with pytest.raises(ValidationError):
        service.create_task("c", estimated_minutes="abc")
    with pytest.raises(ValidationError):
        service.create_task("d", estimated_minutes=30.5)


def test_reject_nonexistent_project(service):
    with pytest.raises(ProjectNotFoundError):
        service.create_task("t", project_id=999)


# --- update ----------------------------------------------------------------

def test_update_only_one_field(service):
    task = service.create_task("orig", description="keep me", priority="low")
    updated = service.update_task(task["id"], status="in_progress")
    assert updated["status"] == "in_progress"
    # Untouched fields preserved.
    assert updated["description"] == "keep me"
    assert updated["priority"] == "low"
    assert updated["title"] == "orig"


def test_update_rejects_invalid_field_value(service):
    task = service.create_task("t")
    with pytest.raises(ValidationError):
        service.update_task(task["id"], priority="nope")


def test_update_rejects_unknown_field(service):
    task = service.create_task("t")
    with pytest.raises(ValidationError):
        service.update_task(task["id"], banana=1)


def test_update_nonexistent_task(service):
    with pytest.raises(TaskNotFoundError):
        service.update_task(12345, status="todo")


def test_update_to_completed_stamps_completed_at(service):
    task = service.create_task("t")
    assert task["completed_at"] is None
    updated = service.update_task(task["id"], status="completed")
    assert updated["completed_at"] is not None


# --- delete ----------------------------------------------------------------

def test_delete_existing_task(service):
    task = service.create_task("to delete")
    result = service.delete_task(task["id"])
    assert result["deleted"] is True
    with pytest.raises(TaskNotFoundError):
        service.get_task_by_id(task["id"])


def test_delete_nonexistent_task(service):
    with pytest.raises(TaskNotFoundError):
        service.delete_task(999)


# --- reads -----------------------------------------------------------------

def test_get_task_by_id(service):
    task = service.create_task("findme")
    fetched = service.get_task_by_id(task["id"])
    assert fetched["id"] == task["id"]
    assert fetched["title"] == "findme"


def test_get_task_by_id_missing(service):
    with pytest.raises(TaskNotFoundError):
        service.get_task_by_id(404)


def test_get_task_by_title_case_insensitive(service):
    service.create_task("Deploy Site")
    fetched = service.get_task_by_title("deploy site")
    assert fetched["title"] == "Deploy Site"


def test_get_task_by_title_missing(service):
    with pytest.raises(TaskNotFoundError):
        service.get_task_by_title("ghost")


# --- filters ---------------------------------------------------------------

def test_filter_by_status(service):
    service.create_task("a", status="todo")
    service.create_task("b", status="blocked")
    blocked = service.get_tasks(status="blocked")
    assert len(blocked) == 1
    assert blocked[0]["title"] == "b"


def test_filter_by_priority(service):
    service.create_task("a", priority="low")
    service.create_task("b", priority="critical")
    crit = service.list_tasks_by_priority("critical")
    assert [t["title"] for t in crit] == ["b"]


def test_filter_by_project(service):
    execute("INSERT INTO projects (name) VALUES (?);", ("Other",))  # id = 2
    service.create_task("p1", project_id=1)
    service.create_task("p2", project_id=2)
    assert [t["title"] for t in service.list_tasks_by_project(1)] == ["p1"]


def test_filters_are_combinable(service):
    service.create_task("x", status="todo", priority="high", project_id=1)
    service.create_task("y", status="todo", priority="low", project_id=1)
    result = service.get_tasks(status="todo", priority="high", project_id=1)
    assert [t["title"] for t in result] == ["x"]


def test_overdue_filter(service):
    service.create_task("past-open", due_date=_iso(-2), status="todo")
    service.create_task("past-done", due_date=_iso(-2), status="completed")
    service.create_task("future", due_date=_iso(5), status="todo")
    service.create_task("no-date", status="todo")
    overdue = service.list_overdue_tasks()
    titles = {t["title"] for t in overdue}
    assert titles == {"past-open"}  # done/future/no-date excluded


# --- return type -----------------------------------------------------------

def test_returned_objects_are_dicts(service):
    task = service.create_task("d")
    assert isinstance(task, dict)
    assert isinstance(service.get_task_by_id(task["id"]), dict)
    listed = service.get_tasks()
    assert isinstance(listed, list) and all(isinstance(t, dict) for t in listed)
