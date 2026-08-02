"""Domain exceptions for the service layer.

These are raised by services and are safe to surface to callers (and, later,
to translate into tool error responses). Low-level ``sqlite3`` / database
exceptions must never escape the service layer; they are caught and either
handled or re-raised as one of these.
"""

from __future__ import annotations


class ServiceError(Exception):
    """Base class for every error raised by the ``services`` package."""


class ValidationError(ServiceError):
    """Raised when input violates a business rule (bad status, empty title...)."""


class TaskNotFoundError(ServiceError):
    """Raised when a task cannot be found by the given identifier or title."""


class ProjectNotFoundError(ServiceError):
    """Raised when a referenced ``project_id`` does not exist.

    Projects are never created implicitly; callers must create them first.
    """


class ServiceDBError(ServiceError):
    """Wraps an unexpected database failure so ``database``/``sqlite3``
    exception types never leak past the service boundary."""
