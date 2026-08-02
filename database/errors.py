"""Database exception types.

Kept small on purpose. The repository layer translates low-level
``sqlite3`` errors into these so callers depend on our own hierarchy
rather than on sqlite3 internals.
"""

from __future__ import annotations


class DatabaseError(Exception):
    """Base class for every error raised by the ``database`` package."""


class ConnectionError(DatabaseError):
    """Raised when a SQLite connection cannot be opened or configured."""


class IntegrityError(DatabaseError):
    """Raised on constraint violations (e.g. duplicate ``projects.name``).

    Subclasses :class:`DatabaseError`, so callers may catch either one.
    """
