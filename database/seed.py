"""Idempotent demo-data seeding.

:func:`seed_database` inserts a small, realistic set of projects and tasks
**only when the database is empty** (no projects present). It deliberately
inserts **no work logs and no completed tasks** — progress and work history
must come from real user activity, never from seed data. This keeps the
"database is the source of truth" invariant honest.
"""

from __future__ import annotations

import sqlite3

from .connection import get_connection
from .errors import DatabaseError, IntegrityError

# (name, description, status, deadline)
_SEED_PROJECTS: tuple[tuple[str, str, str, str | None], ...] = (
    (
        "ayarlicazhocam",
        "Personal AI productivity assistant (this project).",
        "active",
        None,
    ),
    (
        "Face Login",
        "Face-recognition based authentication project.",
        "active",
        None,
    ),
    (
        "Portfolio",
        "Personal portfolio website.",
        "active",
        None,
    ),
)

# (project_name, title, description, status, priority, due_date, estimated_minutes)
_SEED_TASKS: tuple[tuple[str, str, str, str, str, str | None, int | None], ...] = (
    (
        "ayarlicazhocam",
        "Design the SQLite schema",
        "Define projects, tasks, plans, and work_logs tables.",
        "todo",
        "high",
        None,
        90,
    ),
    (
        "ayarlicazhocam",
        "Write the productivity system prompt",
        "Draft the assistant identity and tool-use rules.",
        "todo",
        "medium",
        None,
        60,
    ),
    (
        "Face Login",
        "Prepare README architecture section",
        "Document the registration and login flow.",
        "todo",
        "high",
        None,
        45,
    ),
    (
        "Face Login",
        "Validate login threshold",
        "Test false-acceptance edge cases.",
        "todo",
        "medium",
        None,
        120,
    ),
    (
        "Portfolio",
        "Deploy the portfolio site",
        "Publish the site once the projects page is complete.",
        "todo",
        "low",
        None,
        30,
    ),
)


def is_empty(conn: sqlite3.Connection | None = None) -> bool:
    """Return ``True`` when the ``projects`` table has no rows."""
    connection = conn if conn is not None else get_connection()
    try:
        row = connection.execute("SELECT COUNT(*) AS n FROM projects;").fetchone()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Could not check whether database is empty: {exc}") from exc
    count = row["n"] if isinstance(row, sqlite3.Row) else row[0]
    return count == 0


def seed_database(conn: sqlite3.Connection | None = None) -> bool:
    """Seed demo data if the database is empty.

    Returns ``True`` if data was inserted, ``False`` if seeding was skipped
    because the database already contained projects. Idempotent: calling it
    repeatedly inserts data at most once.
    """
    connection = conn if conn is not None else get_connection()

    if not is_empty(connection):
        return False

    try:
        project_ids: dict[str, int] = {}
        for name, description, status, deadline in _SEED_PROJECTS:
            cursor = connection.execute(
                "INSERT INTO projects (name, description, status, deadline) "
                "VALUES (?, ?, ?, ?);",
                (name, description, status, deadline),
            )
            project_ids[name] = int(cursor.lastrowid)

        for (
            project_name,
            title,
            description,
            status,
            priority,
            due_date,
            estimated_minutes,
        ) in _SEED_TASKS:
            connection.execute(
                "INSERT INTO tasks "
                "(project_id, title, description, status, priority, due_date, "
                " estimated_minutes) VALUES (?, ?, ?, ?, ?, ?, ?);",
                (
                    project_ids[project_name],
                    title,
                    description,
                    status,
                    priority,
                    due_date,
                    estimated_minutes,
                ),
            )

        connection.commit()
    except sqlite3.IntegrityError as exc:
        connection.rollback()
        raise IntegrityError(f"Seeding failed: {exc}") from exc
    except sqlite3.Error as exc:
        connection.rollback()
        raise DatabaseError(f"Seeding failed: {exc}") from exc

    return True
