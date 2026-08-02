"""Database layer: SQLite connection, schema, generic repository, and seed.

This is the single source of truth for current state. No SQL lives outside
this package, and no business logic lives inside it (Phase 2 scope).

Public API::

    from database import (
        get_connection, close_connection,
        initialize_database,
        seed_database,
        execute, execute_many, fetch_one, fetch_all,
        DatabaseError, IntegrityError, ConnectionError,
    )
"""

from __future__ import annotations

from .connection import close_connection, get_connection, resolve_db_path
from .errors import ConnectionError, DatabaseError, IntegrityError
from .repository import execute, execute_many, fetch_all, fetch_one
from .schema import initialize_database
from .seed import seed_database

__all__ = [
    "get_connection",
    "close_connection",
    "resolve_db_path",
    "initialize_database",
    "seed_database",
    "execute",
    "execute_many",
    "fetch_one",
    "fetch_all",
    "DatabaseError",
    "IntegrityError",
    "ConnectionError",
]
