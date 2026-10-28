"""Class schedule persistence: SQL for weekly recurring schedules (US 2.9).

Ownership enforced through the courses join in every statement.
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class ScheduleRepository:
    """SQL access for class_schedules. No business logic lives here."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_schedule(
        self,
        *,
        user_id: str,
        course_id: str,
        day_of_week: int,
        start_time: str,
        end_time: str,
        location: str | None,
        start_date: str,
        end_date: str,
    ) -> dict[str, Any] | None:
        """Insert a weekly slot under an owned course (None when missing)."""
        async with self._session.begin():
            owned = await self._session.execute(
                text("SELECT 1 FROM courses WHERE id = :course_id AND user_id = :user_id"),
                {"course_id": course_id, "user_id": user_id},
            )
            if owned.scalar_one_or_none() is None:
                return None
            result = await self._session.execute(
                text(
                    "INSERT INTO class_schedules "
                    "(course_id, day_of_week, start_time, end_time, location, start_date, end_date) "
                    "VALUES (:course_id, :day_of_week, :start_time, :end_time, :location, "
                    ":start_date, :end_date) "
                    "RETURNING id, course_id, day_of_week, start_time, end_time, location, "
                    "start_date, end_date, created_at, updated_at"
                ),
                {
                    "course_id": course_id,
                    "day_of_week": day_of_week,
                    "start_time": start_time,
                    "end_time": end_time,
                    "location": location,
                    "start_date": start_date,
                    "end_date": end_date,
                },
            )
            row = result.mappings().one()
        return dict(row)

    async def list_schedules(self, user_id: str, course_id: str) -> list[dict[str, Any]]:
        """List an owned course's weekly slots, weekday then start time."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT s.id, s.course_id, s.day_of_week, s.start_time, s.end_time, "
                    "s.location, s.start_date, s.end_date, s.created_at, s.updated_at, "
                    "c.name AS course_name "
                    "FROM class_schedules s JOIN courses c ON c.id = s.course_id "
                    "WHERE s.course_id = :course_id AND c.user_id = :user_id "
                    "ORDER BY s.day_of_week ASC, s.start_time ASC"
                ),
                {"course_id": course_id, "user_id": user_id},
            )
            rows = result.mappings().all()
        return [dict(row) for row in rows]

    async def get_schedule(self, user_id: str, schedule_id: str) -> dict[str, Any] | None:
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT s.id, s.course_id, s.day_of_week, s.start_time, s.end_time, "
                    "s.location, s.start_date, s.end_date, s.created_at, s.updated_at "
                    "FROM class_schedules s JOIN courses c ON c.id = s.course_id "
                    "WHERE s.id = :schedule_id AND c.user_id = :user_id"
                ),
                {"schedule_id": schedule_id, "user_id": user_id},
            )
            row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def list_day_schedules_for_owner(
        self, user_id: str, day_of_week: int
    ) -> list[dict[str, Any]]:
        """All the owner's slots on a weekday across their courses (overlap scan)."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT s.id, s.course_id, s.day_of_week, s.start_time, s.end_time, "
                    "s.location, s.start_date, s.end_date, c.name AS course_name "
                    "FROM class_schedules s JOIN courses c ON c.id = s.course_id "
                    "WHERE c.user_id = :user_id AND s.day_of_week = :day_of_week"
                ),
                {"user_id": user_id, "day_of_week": day_of_week},
            )
            rows = result.mappings().all()
        return [dict(row) for row in rows]

    async def update_schedule(
        self, user_id: str, schedule_id: str, changes: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Apply whitelisted changes to an owned weekly slot."""
        allowed = ("day_of_week", "start_time", "end_time", "location", "start_date", "end_date")
        safe_changes = {k: v for k, v in changes.items() if k in allowed}
        if not safe_changes:
            return await self.get_schedule(user_id, schedule_id)

        set_clause = ", ".join(f"s.{column} = :{column}" for column in safe_changes)
        params: dict[str, Any] = {**safe_changes, "schedule_id": schedule_id, "user_id": user_id}
        async with self._session.begin():
            await self._session.execute(
                text(
                    f"UPDATE class_schedules s SET {set_clause}, s.updated_at = CURRENT_TIMESTAMP "
                    "FROM courses c WHERE s.course_id = c.id "
                    "AND s.id = :schedule_id AND c.user_id = :user_id"
                ),
                params,
            )
        return await self.get_schedule(user_id, schedule_id)

    async def delete_schedule(self, user_id: str, schedule_id: str) -> bool:
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "DELETE FROM class_schedules s USING courses c "
                    "WHERE s.course_id = c.id AND s.id = :schedule_id AND c.user_id = :user_id"
                ),
                {"schedule_id": schedule_id, "user_id": user_id},
            )
            deleted = int(getattr(result, "rowcount", 0) or 0) > 0
        return deleted
