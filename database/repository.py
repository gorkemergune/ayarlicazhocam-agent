"""Generic, parameterized SQL helpers.

This layer contains **no business logic** — only thin wrappers around the
cached connection that guarantee parameterized queries and consistent
error translation. Higher layers (tools, services) build on these.

Rule: every SQL value goes through the ``params`` argument. String
formatting / f-strings for SQL values is never used here and must not be
used by callers.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Sequence
from typing import Any

from .connection import get_connection
from .errors import DatabaseError, IntegrityError

# A single query parameter set. sqlite3 accepts a sequence (qmark style) or a
# mapping (named style); we support both.
Params = Sequence[Any] | dict[str, Any]

# The connection is shared across Gradio worker threads (opened with
# ``check_same_thread=False``). This re-entrant lock serializes every access so
# two threads never use the connection/its cursors concurrently.
_LOCK = threading.RLock()


def _execute(sql: str, params: Params) -> sqlite3.Cursor:
    """Run ``sql`` with ``params`` and return the cursor (no commit)."""
    conn = get_connection()
    try:
        return conn.execute(sql, params)
    except sqlite3.IntegrityError as exc:
        raise IntegrityError(str(exc)) from exc
    except sqlite3.Error as exc:
        raise DatabaseError(str(exc)) from exc


def execute(sql: str, params: Params = ()) -> int:
    """Execute a write statement and commit.

    Returns ``cursor.lastrowid`` for INSERTs (or the last row id otherwise),
    which callers may ignore.
    """
    with _LOCK:
        conn = get_connection()
        cursor = _execute(sql, params)
        try:
            conn.commit()
        except sqlite3.Error as exc:  # pragma: no cover - commit rarely fails alone
            raise DatabaseError(str(exc)) from exc
        return cursor.lastrowid if cursor.lastrowid is not None else -1


def execute_many(sql: str, seq_of_params: Sequence[Params]) -> None:
    """Execute a write statement for many parameter sets, then commit."""
    with _LOCK:
        conn = get_connection()
        try:
            conn.executemany(sql, seq_of_params)
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise IntegrityError(str(exc)) from exc
        except sqlite3.Error as exc:
            raise DatabaseError(str(exc)) from exc


def fetch_one(sql: str, params: Params = ()) -> sqlite3.Row | None:
    """Return the first matching row, or ``None`` if there are no rows."""
    with _LOCK:
        return _execute(sql, params).fetchone()


def fetch_all(sql: str, params: Params = ()) -> list[sqlite3.Row]:
    """Return all matching rows as a list (possibly empty)."""
    with _LOCK:
        return list(_execute(sql, params).fetchall())
