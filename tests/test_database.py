"""Tests for the SQLite persistence layer (Phase 2).

Each test points ``DATABASE_PATH`` at a fresh temp file and resets the
cached connection, so tests are isolated and never touch the real
``data/ayarlicazhocam.db``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import database
from database import (
    IntegrityError,
    close_connection,
    execute,
    fetch_all,
    fetch_one,
    get_connection,
    initialize_database,
    seed_database,
)

EXPECTED_TABLES = {
    "projects",
    "tasks",
    "daily_plans",
    "daily_plan_items",
    "work_logs",
}


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Provide an initialized, empty temp database and clean up afterwards."""
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    # Ensure no connection from a previous test leaks in.
    close_connection()
    conn = get_connection()
    initialize_database(conn)
    yield conn
    close_connection()


def _table_names(conn) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table';"
    ).fetchall()
    return {r["name"] for r in rows}


def test_database_initializes_and_file_created(db, tmp_path):
    assert (tmp_path / "test.db").exists()


def test_all_tables_exist(db):
    assert EXPECTED_TABLES.issubset(_table_names(db))


def test_expected_indexes_exist(db):
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name LIKE 'idx_%';"
    ).fetchall()
    names = {r["name"] for r in rows}
    assert "idx_tasks_project_id" in names
    assert "idx_work_logs_task_id" in names


def test_foreign_keys_enabled(db):
    assert db.execute("PRAGMA foreign_keys;").fetchone()[0] == 1


def test_foreign_key_enforced_on_insert(db):
    # Inserting a work_log for a non-existent task must violate the FK.
    with pytest.raises(IntegrityError):
        execute(
            "INSERT INTO work_logs (task_id, log_date, minutes_spent) "
            "VALUES (?, ?, ?);",
            (999, "2026-08-02", 30),
        )


def test_initialize_is_idempotent(db):
    # Running twice must not raise or drop data.
    execute("INSERT INTO projects (name) VALUES (?);", ("KeepMe",))
    initialize_database(db)
    row = fetch_one("SELECT name FROM projects WHERE name = ?;", ("KeepMe",))
    assert row is not None and row["name"] == "KeepMe"


def test_seed_inserts_when_empty(db):
    inserted = seed_database(db)
    assert inserted is True
    projects = fetch_all("SELECT name FROM projects;")
    names = {p["name"] for p in projects}
    assert {"ayarlicazhocam", "Face Login", "Portfolio"}.issubset(names)
    # A few tasks were seeded...
    tasks = fetch_all("SELECT id FROM tasks;")
    assert len(tasks) > 0
    # ...but NO work logs (progress must be real, not seeded).
    logs = fetch_all("SELECT id FROM work_logs;")
    assert logs == []


def test_seed_runs_only_once(db):
    assert seed_database(db) is True
    project_count_first = fetch_one("SELECT COUNT(*) AS n FROM projects;")["n"]
    # Second call must be a no-op.
    assert seed_database(db) is False
    project_count_second = fetch_one("SELECT COUNT(*) AS n FROM projects;")["n"]
    assert project_count_first == project_count_second


def test_duplicate_project_name_rejected(db):
    execute("INSERT INTO projects (name) VALUES (?);", ("Unique",))
    with pytest.raises(IntegrityError):
        execute("INSERT INTO projects (name) VALUES (?);", ("Unique",))


def test_parameterized_query_roundtrip(db):
    # Values that would break naive string formatting must be handled safely.
    tricky = "O'Brien; DROP TABLE projects;--"
    new_id = execute("INSERT INTO projects (name) VALUES (?);", (tricky,))
    assert new_id > 0
    row = fetch_one("SELECT name FROM projects WHERE id = ?;", (new_id,))
    assert row["name"] == tricky
    # The table still exists (injection did not execute).
    assert "projects" in _table_names(db)


def test_relative_db_path_anchored_to_project_root(tmp_path, monkeypatch):
    # A relative DATABASE_PATH must resolve to the project root, NOT the current
    # working directory, so launching from anywhere uses the same database file.
    monkeypatch.setenv("DATABASE_PATH", "data/ayarlicazhocam.db")
    monkeypatch.chdir(tmp_path)  # launch from an unrelated directory
    close_connection()
    path = database.resolve_db_path()
    assert path.endswith("ayarlicazhocam.db")
    assert Path(path).is_absolute()
    assert Path(path).parent.name == "data"
    assert Path(path).parent.exists()  # parent dir auto-created
    # Crucially: it was NOT created relative to the (changed) CWD.
    assert str(tmp_path) not in path
    assert not (tmp_path / "data").exists()
    close_connection()
