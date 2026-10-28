"""Calendar aggregation persistence (US: aggregated calendar).

Reads the owner's schedules, exams, and assignment deadlines in a date range.
Ownership is enforced inside every query (courses join).
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class CalendarRepository:
    """SQL access for calendar aggregation. No business logic lives here."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_schedules_in_range(
        self, user_id: str, start_date: str, end_date: str
    ) -> list[dict[str, Any]]:
        """Weekly slots active at any point in [start_date, end_date]."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT s.id, s.course_id, c.name AS course_name, s.day_of_week, "
                    "s.start_time, s.end_time, s.location, s.start_date, s.end_date "
                    "FROM class_schedules s JOIN courses c ON c.id = s.course_id "
                    "WHERE c.user_id = :user_id "
                    "AND s.start_date <= :end_date AND s.end_date >= :start_date"
                ),
                {"user_id": user_id, "start_date": start_date, "end_date": end_date},
            )
            rows = result.mappings().all()
        return [dict(row) for row in rows]

    async def list_exams_in_range(
        self, user_id: str, start_date: str, end_date: str
    ) -> list[dict[str, Any]]:
        """Exams whose local datetime falls within the inclusive date range."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT e.id, e.course_id, c.name AS course_name, e.title, e.exam_at, "
                    "e.duration_minutes, e.location "
                    "FROM exams e JOIN courses c ON c.id = e.course_id "
                    "WHERE c.user_id = :user_id "
                    "AND (e.exam_at AT TIME ZONE 'UTC')::date BETWEEN :start_date AND :end_date"
                ),
                {"user_id": user_id, "start_date": start_date, "end_date": end_date},
            )
            rows = result.mappings().all()
        return [dict(row) for row in rows]

    async def list_assignment_deadlines_in_range(
        self, user_id: str, start_date: str, end_date: str
    ) -> list[dict[str, Any]]:
        """Assignment deadlines within the inclusive date range."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT a.id, a.course_id, c.name AS course_name, a.title, a.due_at, "
                    "a.priority, a.status "
                    "FROM assignments a JOIN courses c ON c.id = a.course_id "
                    "WHERE c.user_id = :user_id AND a.due_at IS NOT NULL "
                    "AND (a.due_at AT TIME ZONE 'UTC')::date BETWEEN :start_date AND :end_date"
                ),
                {"user_id": user_id, "start_date": start_date, "end_date": end_date},
            )
            rows = result.mappings().all()
        return [dict(row) for row in rows]
