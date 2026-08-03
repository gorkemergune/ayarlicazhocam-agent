"""Task business logic.

``TaskService`` is the single entry point for every task-related operation.
Neither future tools nor the LLM should touch ``database.repository`` directly;
they go through this service. All validation and business rules live here, and
all results are returned as plain ``dict`` objects (never ``sqlite3.Row``) so
they serialize straight to JSON.

Layering::

    Tool Layer  ->  TaskService  ->  repository helpers  ->  SQLite

The service only uses the generic repository helpers (``execute``,
``fetch_one``, ``fetch_all``); it never writes raw SQL against a connection and
never lets a ``sqlite3`` / ``DatabaseError`` leak to callers.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Final

from database import (
    DatabaseError,
    IntegrityError,
    execute,
    fetch_all,
    fetch_one,
)

from .errors import (
    ProjectNotFoundError,
    ServiceDBError,
    TaskNotFoundError,
    ValidationError,
)

# --- Allowed value sets (business rules) -----------------------------------

ALLOWED_STATUSES: Final[frozenset[str]] = frozenset(
    {"todo", "in_progress", "blocked", "completed", "cancelled"}
)
ALLOWED_PRIORITIES: Final[frozenset[str]] = frozenset(
    {"low", "medium", "high", "critical"}
)
ALLOWED_ENERGY_LEVELS: Final[frozenset[str]] = frozenset({"low", "medium", "high"})

# Columns a caller may set via update_task(). project_id/completed_at are handled
# with extra rules but are still updatable.
_UPDATABLE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "title",
        "description",
        "project_id",
        "status",
        "priority",
        "due_date",
        "estimated_minutes",
        "actual_minutes",
        "energy_level",
        "blocker",
        "completed_at",
    }
)

# Statuses that mean "this task is not actionable anymore" (excluded from overdue).
_CLOSED_STATUSES: Final[frozenset[str]] = frozenset({"completed", "cancelled"})

# Sentinel so update_task can tell "not provided" apart from "explicitly None".
_UNSET: Final = object()


class TaskService:
    """Task-related business logic over the Phase 2 repository helpers."""

    # ---- Validation helpers ------------------------------------------------

    @staticmethod
    def _validate_title(title: Any) -> str:
        if not isinstance(title, str):
            raise ValidationError("Title is required and must be a string.")
        trimmed = title.strip()
        if not trimmed:
            raise ValidationError("Title cannot be empty.")
        return trimmed

    @staticmethod
    def _validate_status(status: Any) -> str:
        if status not in ALLOWED_STATUSES:
            raise ValidationError(
                f"Invalid status {status!r}. Allowed: "
                f"{', '.join(sorted(ALLOWED_STATUSES))}."
            )
        return status

    @staticmethod
    def _validate_priority(priority: Any) -> str:
        if priority not in ALLOWED_PRIORITIES:
            raise ValidationError(
                f"Invalid priority {priority!r}. Allowed: "
                f"{', '.join(sorted(ALLOWED_PRIORITIES))}."
            )
        return priority

    @staticmethod
    def _validate_energy_level(energy_level: Any) -> str | None:
        if energy_level is None:
            return None
        if energy_level not in ALLOWED_ENERGY_LEVELS:
            raise ValidationError(
                f"Invalid energy_level {energy_level!r}. Allowed: "
                f"{', '.join(sorted(ALLOWED_ENERGY_LEVELS))} (or null)."
            )
        return energy_level

    @staticmethod
    def _validate_due_date(due_date: Any) -> str | None:
        if due_date is None:
            return None
        if not isinstance(due_date, str):
            raise ValidationError("due_date must be a string in YYYY-MM-DD format.")
        value = due_date.strip()
        try:
            # strptime enforces exactly YYYY-MM-DD and rejects impossible dates.
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError as exc:
            raise ValidationError(
                f"Invalid due_date {due_date!r}. Expected ISO format YYYY-MM-DD."
            ) from exc
        return value

    @staticmethod
    def _validate_minutes(value: Any, field_name: str) -> int | None:
        if value is None:
            return None
        # bool is a subclass of int; reject it explicitly.
        if isinstance(value, bool):
            raise ValidationError(f"{field_name} must be an integer >= 0.")
        # LLMs frequently send numbers as strings ("30") or floats (30.0).
        # Coerce those to a non-negative int; reject anything non-numeric.
        if isinstance(value, str):
            text = value.strip()
            if not text.lstrip("-").isdigit():
                raise ValidationError(f"{field_name} must be an integer >= 0.")
            value = int(text)
        elif isinstance(value, float):
            if not value.is_integer():
                raise ValidationError(
                    f"{field_name} must be a whole number of minutes."
                )
            value = int(value)
        elif not isinstance(value, int):
            raise ValidationError(f"{field_name} must be an integer >= 0.")
        if value < 0:
            raise ValidationError(f"{field_name} must be >= 0.")
        return value

    @staticmethod
    def _verify_project_exists(project_id: Any) -> int | None:
        if project_id is None:
            return None
        if isinstance(project_id, bool) or not isinstance(project_id, int):
            raise ValidationError("project_id must be an integer.")
        row = fetch_one("SELECT id FROM projects WHERE id = ?;", (project_id,))
        if row is None:
            raise ProjectNotFoundError(
                f"Project id {project_id} does not exist. "
                "Projects are never created automatically."
            )
        return project_id

    # ---- Serialization -----------------------------------------------------

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        return dict(row)

    # ---- Reads -------------------------------------------------------------

    def get_task_by_id(self, task_id: int) -> dict[str, Any]:
        """Return the task with ``task_id`` as a dict, or raise if missing."""
        row = fetch_one("SELECT * FROM tasks WHERE id = ?;", (task_id,))
        if row is None:
            raise TaskNotFoundError(f"No task found with id {task_id}.")
        return self._row_to_dict(row)

    def get_task_by_title(self, title: str) -> dict[str, Any]:
        """Return the task whose title matches ``title`` (case-insensitive).

        Raises :class:`TaskNotFoundError` if no task matches. If several tasks
        share the title, the lowest-id match is returned; richer ambiguity
        handling (returning all candidates) is left to the future tool layer
        via :meth:`get_tasks`.
        """
        if not isinstance(title, str) or not title.strip():
            raise ValidationError("title is required to look up a task by title.")
        row = fetch_one(
            "SELECT * FROM tasks WHERE title = ? COLLATE NOCASE ORDER BY id LIMIT 1;",
            (title.strip(),),
        )
        if row is None:
            raise TaskNotFoundError(f"No task found with title {title!r}.")
        return self._row_to_dict(row)

    def get_tasks(
        self,
        *,
        status: str | None = None,
        priority: str | None = None,
        project_id: int | None = None,
        overdue: bool = False,
    ) -> list[dict[str, Any]]:
        """Return tasks matching the given, combinable filters.

        ``overdue`` selects tasks whose ``due_date`` is before today and whose
        status is still actionable (not completed/cancelled).
        """
        clauses: list[str] = []
        params: list[Any] = []

        if status is not None:
            self._validate_status(status)
            clauses.append("status = ?")
            params.append(status)

        if priority is not None:
            self._validate_priority(priority)
            clauses.append("priority = ?")
            params.append(priority)

        if project_id is not None:
            clauses.append("project_id = ?")
            params.append(project_id)

        if overdue:
            today = date.today().isoformat()
            clauses.append("due_date IS NOT NULL AND due_date < ?")
            params.append(today)
            placeholders = ", ".join("?" for _ in _CLOSED_STATUSES)
            clauses.append(f"status NOT IN ({placeholders})")
            params.extend(sorted(_CLOSED_STATUSES))

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM tasks{where} ORDER BY id;"
        rows = fetch_all(sql, tuple(params))
        return [self._row_to_dict(r) for r in rows]

    def list_tasks_by_project(self, project_id: int) -> list[dict[str, Any]]:
        """Return all tasks belonging to ``project_id``."""
        return self.get_tasks(project_id=project_id)

    def list_tasks_by_priority(self, priority: str) -> list[dict[str, Any]]:
        """Return all tasks with the given priority."""
        return self.get_tasks(priority=priority)

    def list_overdue_tasks(self) -> list[dict[str, Any]]:
        """Return all actionable tasks whose due date has passed."""
        return self.get_tasks(overdue=True)

    # ---- Writes ------------------------------------------------------------

    def create_task(
        self,
        title: str,
        *,
        description: str | None = None,
        project_id: int | None = None,
        status: str = "todo",
        priority: str = "medium",
        due_date: str | None = None,
        estimated_minutes: int | None = None,
        actual_minutes: int | None = None,
        energy_level: str | None = None,
        blocker: str | None = None,
    ) -> dict[str, Any]:
        """Validate and insert a new task, returning the stored row as a dict."""
        title = self._validate_title(title)
        status = self._validate_status(status)
        priority = self._validate_priority(priority)
        due_date = self._validate_due_date(due_date)
        estimated_minutes = self._validate_minutes(
            estimated_minutes, "estimated_minutes"
        )
        actual_minutes = self._validate_minutes(actual_minutes, "actual_minutes")
        energy_level = self._validate_energy_level(energy_level)
        project_id = self._verify_project_exists(project_id)

        # Completing a task on creation records the completion timestamp.
        completed_at = self._now() if status == "completed" else None

        try:
            new_id = execute(
                "INSERT INTO tasks "
                "(project_id, title, description, status, priority, due_date, "
                " estimated_minutes, actual_minutes, energy_level, blocker, "
                " completed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
                (
                    project_id,
                    title,
                    description,
                    status,
                    priority,
                    due_date,
                    estimated_minutes,
                    actual_minutes,
                    energy_level,
                    blocker,
                    completed_at,
                ),
            )
        except IntegrityError as exc:
            # e.g. a project deleted between the existence check and insert.
            raise ProjectNotFoundError(str(exc)) from exc
        except DatabaseError as exc:  # pragma: no cover - unexpected DB failure
            raise ServiceDBError(str(exc)) from exc

        return self.get_task_by_id(new_id)

    def update_task(self, task_id: int, **fields: Any) -> dict[str, Any]:
        """Update only the provided fields of a task, returning the new state.

        Unspecified fields are left untouched. Passing ``None`` for a nullable
        field clears it. Raises :class:`TaskNotFoundError` if the task is
        missing and :class:`ValidationError` for unknown or invalid fields.
        """
        # Ensure the task exists first (clear error, and lets us default
        # completed_at when a caller sets status=completed without a timestamp).
        current = self.get_task_by_id(task_id)

        unknown = set(fields) - _UPDATABLE_FIELDS
        if unknown:
            raise ValidationError(
                f"Unknown field(s): {', '.join(sorted(unknown))}. "
                f"Updatable: {', '.join(sorted(_UPDATABLE_FIELDS))}."
            )
        if not fields:
            raise ValidationError("No fields provided to update.")

        validated: dict[str, Any] = {}
        for name, value in fields.items():
            validated[name] = self._validate_update_field(name, value)

        # If moving to 'completed' without an explicit completed_at, stamp now.
        if validated.get("status") == "completed" and "completed_at" not in validated:
            if current.get("completed_at") is None:
                validated["completed_at"] = self._now()

        set_columns = ", ".join(f"{col} = ?" for col in validated)
        params = list(validated.values())
        params.append(task_id)

        try:
            execute(
                f"UPDATE tasks SET {set_columns}, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?;",
                tuple(params),
            )
        except IntegrityError as exc:
            raise ProjectNotFoundError(str(exc)) from exc
        except DatabaseError as exc:  # pragma: no cover - unexpected DB failure
            raise ServiceDBError(str(exc)) from exc

        return self.get_task_by_id(task_id)

    def delete_task(self, task_id: int) -> dict[str, Any]:
        """Delete a task by id.

        Raises :class:`TaskNotFoundError` if no such task exists. Returns a
        small confirmation dict on success.
        """
        # Confirm existence for a meaningful error instead of a silent no-op.
        self.get_task_by_id(task_id)
        execute("DELETE FROM tasks WHERE id = ?;", (task_id,))
        return {"deleted": True, "id": task_id}

    # ---- Internal ----------------------------------------------------------


    def _validate_update_field(self, name: str, value: Any) -> Any:
        """Validate a single field for update_task, returning the stored value."""
        if name == "title":
            return self._validate_title(value)
        if name == "status":
            return self._validate_status(value)
        if name == "priority":
            return self._validate_priority(value)
        if name == "energy_level":
            return self._validate_energy_level(value)
        if name == "due_date":
            return self._validate_due_date(value)
        if name in ("estimated_minutes", "actual_minutes"):
            return self._validate_minutes(value, name)
        if name == "project_id":
            return self._verify_project_exists(value)
        # description, blocker, completed_at: free-form / nullable text.
        return value

    @staticmethod
    def _now() -> str:
        """Return the current local timestamp as ISO text (matches SQLite)."""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
