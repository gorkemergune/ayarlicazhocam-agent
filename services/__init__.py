"""Service layer: business logic between tools and the repository.

Services own all validation and business rules and return plain ``dict``
results (never ``sqlite3.Row``). Tools and the LLM must talk to services, not
to ``database.repository`` directly. Services must not import Gradio or the
model provider.

Currently implemented: :class:`TaskService` (Phase 3). Planner and review
services come in later phases.
"""

from __future__ import annotations

from .errors import (
    ProjectNotFoundError,
    ServiceDBError,
    ServiceError,
    TaskNotFoundError,
    ValidationError,
)
from .task_service import (
    ALLOWED_ENERGY_LEVELS,
    ALLOWED_PRIORITIES,
    ALLOWED_STATUSES,
    TaskService,
)

__all__ = [
    "TaskService",
    "ALLOWED_STATUSES",
    "ALLOWED_PRIORITIES",
    "ALLOWED_ENERGY_LEVELS",
    "ServiceError",
    "ValidationError",
    "TaskNotFoundError",
    "ProjectNotFoundError",
    "ServiceDBError",
]
