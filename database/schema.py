"""Database schema definition and initialization.

Exposes :func:`initialize_database`, which creates every table and index
using ``CREATE TABLE IF NOT EXISTS`` / ``CREATE INDEX IF NOT EXISTS``. It is
idempotent and never deletes existing data, so it is safe to call on every
startup.

Schema mirrors the specification in ``CLAUDE.md``.
"""

from __future__ import annotations

import sqlite3

from .connection import get_connection
from .errors import DatabaseError

# --- Table definitions -----------------------------------------------------

_PROJECTS = """
CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    description TEXT,
    status      TEXT NOT NULL DEFAULT 'active',
    deadline    TEXT,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

_TASKS = """
CREATE TABLE IF NOT EXISTS tasks (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id        INTEGER,
    title             TEXT NOT NULL,
    description       TEXT,
    status            TEXT NOT NULL DEFAULT 'todo',
    priority          TEXT NOT NULL DEFAULT 'medium',
    due_date          TEXT,
    estimated_minutes INTEGER,
    actual_minutes    INTEGER,
    energy_level      TEXT,
    blocker           TEXT,
    postponed_count   INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at      TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
);
"""

_DAILY_PLANS = """
CREATE TABLE IF NOT EXISTS daily_plans (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_date         TEXT NOT NULL UNIQUE,
    available_minutes INTEGER,
    notes             TEXT,
    created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

_DAILY_PLAN_ITEMS = """
CREATE TABLE IF NOT EXISTS daily_plan_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    daily_plan_id   INTEGER NOT NULL,
    task_id         INTEGER NOT NULL,
    position        INTEGER NOT NULL,
    planned_minutes INTEGER,
    result          TEXT,
    FOREIGN KEY (daily_plan_id) REFERENCES daily_plans(id) ON DELETE CASCADE,
    FOREIGN KEY (task_id)       REFERENCES tasks(id)       ON DELETE CASCADE,
    UNIQUE (daily_plan_id, task_id)
);
"""

_WORK_LOGS = """
CREATE TABLE IF NOT EXISTS work_logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      INTEGER NOT NULL,
    log_date     TEXT NOT NULL,
    minutes_spent INTEGER NOT NULL,
    note         TEXT,
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);
"""

_TABLES: tuple[str, ...] = (
    _PROJECTS,
    _TASKS,
    _DAILY_PLANS,
    _DAILY_PLAN_ITEMS,
    _WORK_LOGS,
)

# --- Index definitions -----------------------------------------------------
# (projects.name and daily_plans.plan_date already get implicit UNIQUE indexes.)

_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_tasks_project_id ON tasks(project_id);",
    "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);",
    "CREATE INDEX IF NOT EXISTS idx_tasks_due_date ON tasks(due_date);",
    "CREATE INDEX IF NOT EXISTS idx_dpi_daily_plan_id ON daily_plan_items(daily_plan_id);",
    "CREATE INDEX IF NOT EXISTS idx_dpi_task_id ON daily_plan_items(task_id);",
    "CREATE INDEX IF NOT EXISTS idx_work_logs_task_id ON work_logs(task_id);",
    "CREATE INDEX IF NOT EXISTS idx_work_logs_log_date ON work_logs(log_date);",
)


def initialize_database(conn: sqlite3.Connection | None = None) -> None:
    """Create all tables and indexes if they do not already exist.

    Idempotent and non-destructive. Uses the cached connection unless an
    explicit ``conn`` is provided (useful for tests).
    """
    connection = conn if conn is not None else get_connection()
    try:
        for statement in (*_TABLES, *_INDEXES):
            connection.execute(statement)
        connection.commit()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to initialize database schema: {exc}") from exc
