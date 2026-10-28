"""Assignment business logic (US 2.7): status transitions with completed_at bookkeeping."""

from typing import Any

from app.repositories.assignment_repo import AssignmentRepository


class AssignmentNotFoundError(Exception):
    """Raised when the assignment (or its owning course) is not the owner's."""


COMPLETED = "completed"


class AssignmentService:
    """Assignment use cases for the authenticated owner."""

    def __init__(self, assignment_repo: AssignmentRepository) -> None:
        self._assignment_repo = assignment_repo

    @classmethod
    def from_session(cls, session: Any) -> "AssignmentService":
        """Compose the service with its repository over one session."""
        return cls(assignment_repo=AssignmentRepository(session))

    async def create_assignment(
        self, user_id: str, course_id: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        created = await self._assignment_repo.create_assignment(
            user_id=user_id, course_id=course_id, **data
        )
        if created is None:
            raise AssignmentNotFoundError("Course not found.")
        return created

    async def list_assignments(
        self, user_id: str, course_id: str, status: str | None = None
    ) -> list[dict[str, Any]]:
        return await self._assignment_repo.list_assignments(user_id, course_id, status)

    async def get_assignment(self, user_id: str, assignment_id: str) -> dict[str, Any]:
        assignment = await self._assignment_repo.get_assignment(user_id, assignment_id)
        if assignment is None:
            raise AssignmentNotFoundError("Assignment not found.")
        return assignment

    async def update_assignment(
        self, user_id: str, assignment_id: str, changes: dict[str, Any]
    ) -> dict[str, Any]:
        """Apply changes; keeps completed_at consistent with the status field.

        Moving to 'completed' stamps completed_at; moving away clears it.
        An explicit completed_at change is rejected (derived field).
        """
        if "completed_at" in changes:
            raise ValueError("completed_at is derived from status; provide status instead.")

        current = await self.get_assignment(user_id, assignment_id)
        new_status = changes.get("status", current["status"])
        if "status" in changes and changes["status"] != current["status"]:
            changes = {
                **changes,
                "completed_at": datetime_utcnow() if new_status == COMPLETED else None,
            }
        updated = await self._assignment_repo.update_assignment(user_id, assignment_id, changes)
        if updated is None:
            raise AssignmentNotFoundError("Assignment not found.")
        return updated

    async def delete_assignment(self, user_id: str, assignment_id: str) -> dict[str, Any]:
        deleted = await self._assignment_repo.delete_assignment(user_id, assignment_id)
        if not deleted:
            raise AssignmentNotFoundError("Assignment not found.")
        return {"deleted": True}


def datetime_utcnow() -> Any:
    """Indirection for completed_at stamping (kept injectable for tests)."""
    from datetime import UTC, datetime

    return datetime.now(tz=UTC)
