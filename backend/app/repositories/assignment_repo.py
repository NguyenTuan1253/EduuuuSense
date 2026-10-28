"""Assignment persistence: SQL for the assignments aggregate (US 2.7).

Ownership is enforced through the courses join in every statement — an
assignment is reachable only through its owner's course.
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class AssignmentRepository:
    """SQL access for assignments. No business logic lives here."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_assignment(
        self,
        *,
        user_id: str,
        course_id: str,
        title: str,
        description: str | None,
        due_at: Any,
        priority: str,
    ) -> dict[str, Any] | None:
        """Insert an assignment under an owned course (None when course missing)."""
        async with self._session.begin():
            owned = await self._session.execute(
                text("SELECT 1 FROM courses WHERE id = :course_id AND user_id = :user_id"),
                {"course_id": course_id, "user_id": user_id},
            )
            if owned.scalar_one_or_none() is None:
                return None
            result = await self._session.execute(
                text(
                    "INSERT INTO assignments (course_id, title, description, due_at, priority) "
                    "VALUES (:course_id, :title, :description, :due_at, :priority) "
                    "RETURNING id, course_id, title, description, due_at, priority, status, "
                    "completed_at, created_at, updated_at"
                ),
                {
                    "course_id": course_id,
                    "title": title,
                    "description": description,
                    "due_at": due_at,
                    "priority": priority,
                },
            )
            row = result.mappings().one()
        return dict(row)

    async def list_assignments(
        self, user_id: str, course_id: str, status: str | None = None
    ) -> list[dict[str, Any]]:
        """List an owned course's assignments, optionally filtered by status."""
        params: dict[str, Any] = {"course_id": course_id, "user_id": user_id}
        status_filter = ""
        if status is not None:
            status_filter = " AND a.status = :status"
            params["status"] = status
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT a.id, a.course_id, a.title, a.description, a.due_at, a.priority, "
                    "a.status, a.completed_at, a.created_at, a.updated_at "
                    "FROM assignments a JOIN courses c ON c.id = a.course_id "
                    "WHERE a.course_id = :course_id AND c.user_id = :user_id"
                    f"{status_filter} ORDER BY a.due_at NULLS LAST, a.created_at DESC"
                ),
                params,
            )
            rows = result.mappings().all()
        return [dict(row) for row in rows]

    async def get_assignment(self, user_id: str, assignment_id: str) -> dict[str, Any] | None:
        """Fetch one assignment via its owned course."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT a.id, a.course_id, a.title, a.description, a.due_at, a.priority, "
                    "a.status, a.completed_at, a.created_at, a.updated_at "
                    "FROM assignments a JOIN courses c ON c.id = a.course_id "
                    "WHERE a.id = :assignment_id AND c.user_id = :user_id"
                ),
                {"assignment_id": assignment_id, "user_id": user_id},
            )
            row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def update_assignment(
        self, user_id: str, assignment_id: str, changes: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Apply whitelisted changes to an owned assignment."""
        allowed = ("title", "description", "due_at", "priority", "status", "completed_at")
        safe_changes = {k: v for k, v in changes.items() if k in allowed}
        if not safe_changes:
            return await self.get_assignment(user_id, assignment_id)

        set_clause = ", ".join(f"a.{column} = :{column}" for column in safe_changes)
        params: dict[str, Any] = {**safe_changes, "assignment_id": assignment_id, "user_id": user_id}
        async with self._session.begin():
            await self._session.execute(
                text(
                    f"UPDATE assignments a SET {set_clause}, a.updated_at = CURRENT_TIMESTAMP "
                    "FROM courses c WHERE a.course_id = c.id "
                    "AND a.id = :assignment_id AND c.user_id = :user_id"
                ),
                params,
            )
        return await self.get_assignment(user_id, assignment_id)

    async def delete_assignment(self, user_id: str, assignment_id: str) -> bool:
        """Delete an owned assignment; return whether a row was removed."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "DELETE FROM assignments a USING courses c "
                    "WHERE a.course_id = c.id AND a.id = :assignment_id AND c.user_id = :user_id"
                ),
                {"assignment_id": assignment_id, "user_id": user_id},
            )
            deleted = int(getattr(result, "rowcount", 0) or 0) > 0
        return deleted
