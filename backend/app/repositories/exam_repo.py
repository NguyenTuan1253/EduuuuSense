"""Exam persistence: SQL for the exams aggregate (US 2.8).

Ownership enforced through the courses join in every statement.
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class ExamRepository:
    """SQL access for exams. No business logic lives here."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_exam(
        self,
        *,
        user_id: str,
        course_id: str,
        title: str,
        exam_at: Any,
        duration_minutes: int | None,
        location: str | None,
        notes: str | None,
    ) -> dict[str, Any] | None:
        """Insert an exam under an owned course (None when course missing)."""
        async with self._session.begin():
            owned = await self._session.execute(
                text("SELECT 1 FROM courses WHERE id = :course_id AND user_id = :user_id"),
                {"course_id": course_id, "user_id": user_id},
            )
            if owned.scalar_one_or_none() is None:
                return None
            result = await self._session.execute(
                text(
                    "INSERT INTO exams (course_id, title, exam_at, duration_minutes, location, notes) "
                    "VALUES (:course_id, :title, :exam_at, :duration_minutes, :location, :notes) "
                    "RETURNING id, course_id, title, exam_at, duration_minutes, location, notes, "
                    "created_at, updated_at"
                ),
                {
                    "course_id": course_id,
                    "title": title,
                    "exam_at": exam_at,
                    "duration_minutes": duration_minutes,
                    "location": location,
                    "notes": notes,
                },
            )
            row = result.mappings().one()
        return dict(row)

    async def list_exams(self, user_id: str, course_id: str) -> list[dict[str, Any]]:
        """List an owned course's exams, soonest first."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT e.id, e.course_id, e.title, e.exam_at, e.duration_minutes, "
                    "e.location, e.notes, e.created_at, e.updated_at "
                    "FROM exams e JOIN courses c ON c.id = e.course_id "
                    "WHERE e.course_id = :course_id AND c.user_id = :user_id "
                    "ORDER BY e.exam_at ASC"
                ),
                {"course_id": course_id, "user_id": user_id},
            )
            rows = result.mappings().all()
        return [dict(row) for row in rows]

    async def get_exam(self, user_id: str, exam_id: str) -> dict[str, Any] | None:
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT e.id, e.course_id, e.title, e.exam_at, e.duration_minutes, "
                    "e.location, e.notes, e.created_at, e.updated_at "
                    "FROM exams e JOIN courses c ON c.id = e.course_id "
                    "WHERE e.id = :exam_id AND c.user_id = :user_id"
                ),
                {"exam_id": exam_id, "user_id": user_id},
            )
            row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def update_exam(
        self, user_id: str, exam_id: str, changes: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Apply whitelisted changes to an owned exam."""
        allowed = ("title", "exam_at", "duration_minutes", "location", "notes")
        safe_changes = {k: v for k, v in changes.items() if k in allowed}
        if not safe_changes:
            return await self.get_exam(user_id, exam_id)

        set_clause = ", ".join(f"e.{column} = :{column}" for column in safe_changes)
        params: dict[str, Any] = {**safe_changes, "exam_id": exam_id, "user_id": user_id}
        async with self._session.begin():
            await self._session.execute(
                text(
                    f"UPDATE exams e SET {set_clause}, e.updated_at = CURRENT_TIMESTAMP "
                    "FROM courses c WHERE e.course_id = c.id "
                    "AND e.id = :exam_id AND c.user_id = :user_id"
                ),
                params,
            )
        return await self.get_exam(user_id, exam_id)

    async def delete_exam(self, user_id: str, exam_id: str) -> bool:
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "DELETE FROM exams e USING courses c "
                    "WHERE e.course_id = c.id AND e.id = :exam_id AND c.user_id = :user_id"
                ),
                {"exam_id": exam_id, "user_id": user_id},
            )
            deleted = int(getattr(result, "rowcount", 0) or 0) > 0
        return deleted
