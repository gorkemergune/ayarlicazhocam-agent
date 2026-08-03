"""SQLite connection management for ayarlicazhocam.

Uses the standard-library ``sqlite3`` only — no ORM, no SQLAlchemy.

A single module-level connection is cached and reused (the app is a
single-user local process). Call :func:`close_connection` to reset it,
which tests use to swap in a temporary database.

The database location is read from the ``DATABASE_PATH`` environment
variable (see ``.env`` / ``.env.example``), defaulting to
``data/ayarlicazhocam.db``. The parent directory is created automatically.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from .errors import ConnectionError as DBConnectionError

DEFAULT_DATABASE_PATH = "data/ayarlicazhocam.db"

# The project root (parent of this ``database`` package). Relative database
# paths are anchored here — NOT to the current working directory — so the same
# SQLite file is used no matter which directory the app is launched from.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Module-level cached connection. ``None`` means "not yet opened".
_connection: sqlite3.Connection | None = None


def _load_env() -> None:
    """Load variables from a local ``.env`` file if python-dotenv is present.

    Never fails: dotenv is optional and a missing ``.env`` is fine.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dotenv is a declared dependency
        return
    load_dotenv()


def resolve_db_path() -> str:
    """Return the configured database path, creating its parent directory.

    Reads ``DATABASE_PATH`` from the environment, falling back to
    :data:`DEFAULT_DATABASE_PATH`. A **relative** path is resolved against the
    project root (so launching from any working directory uses the same file);
    an absolute path is honored as-is. Returns an absolute path. The special
    value ``":memory:"`` is returned untouched.
    """
    _load_env()
    db_path = os.environ.get("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip()
    if not db_path:
        db_path = DEFAULT_DATABASE_PATH
    if db_path == ":memory:":
        return db_path

    path = Path(db_path).expanduser()
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def _configure(conn: sqlite3.Connection) -> None:
    """Apply per-connection settings (row factory + foreign key enforcement)."""
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    """Return the cached SQLite connection, opening it on first use.

    Parameters
    ----------
    db_path:
        Optional explicit path. Used only when opening a new connection;
        if a connection is already cached it is returned as-is. Pass ``None``
        to use :func:`resolve_db_path`.
    """
    global _connection
    if _connection is not None:
        return _connection

    path = db_path if db_path is not None else resolve_db_path()
    try:
        # ``check_same_thread=False`` is required because Gradio serves requests
        # from a pool of worker threads while this connection is created once at
        # startup. Access is serialized by the lock in ``repository.py`` so the
        # single shared connection is used safely across those threads.
        conn = sqlite3.connect(path, check_same_thread=False)
        _configure(conn)
    except sqlite3.Error as exc:
        raise DBConnectionError(f"Could not open database at {path!r}: {exc}") from exc

    _connection = conn
    return _connection


def close_connection() -> None:
    """Close and forget the cached connection, if any.

    Safe to call when no connection is open. After this call the next
    :func:`get_connection` opens a fresh connection.
    """
    global _connection
    if _connection is not None:
        try:
            _connection.close()
        finally:
            _connection = None
