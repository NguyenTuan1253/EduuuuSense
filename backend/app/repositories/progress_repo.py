"""Progress analytics persistence: course_progress + dashboard aggregates."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class ProgressRepository:
    """SQL access for dashboard/progress data. No business logic lives here."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def assignment_counts_by_course(self, user_id: str) -> list[dict[str, Any]]:
        """(course_id, total, completed) for the owner's assignments."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT c.id AS course_id, c.name AS course_name, "
                    "count(a.id) AS total, "
                    "count(a.id) FILTER (WHERE a.status = 'completed') AS completed "
                    "FROM courses c LEFT JOIN assignments a ON a.course_id = c.id "
                    "WHERE c.user_id = :user_id "
                    "GROUP BY c.id, c.name ORDER BY c.created_at"
                ),
                {"user_id": user_id},
            )
            rows = result.mappings().all()
        return [dict(row) for row in rows]

    async def study_item_counts_by_course(self, user_id: str) -> dict[str, tuple[int, int]]:
        """(accepted_or_beyond, total) study items per course (accepted = committed)."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT i.course_id, "
                    "count(*) AS total, "
                    "count(*) FILTER (WHERE i.status IN ('accepted', 'in_progress', 'completed')) AS committed, "
                    "count(*) FILTER (WHERE i.status = 'completed') AS completed "
                    "FROM study_plan_items i JOIN study_plans p ON p.id = i.study_plan_id "
                    "WHERE p.user_id = :user_id AND i.course_id IS NOT NULL "
                    "GROUP BY i.course_id"
                ),
                {"user_id": user_id},
            )
            rows = result.mappings().all()
        return {str(row["course_id"]): (int(row["total"]), int(row["completed"])) for row in rows}

    async def upsert_course_progress(
        self,
        *,
        user_id: str,
        course_id: str,
        assignments_total: int,
        assignments_completed: int,
        study_items_total: int,
        study_items_completed: int,
        progress_percent: float,
        risk_status: str,
    ) -> None:
        async with self._session.begin():
            await self._session.execute(
                text(
                    "INSERT INTO course_progress (user_id, course_id, assignments_total, "
                    "assignments_completed, study_items_total, study_items_completed, "
                    "progress_percent, risk_status, last_calculated_at) "
                    "VALUES (:user_id, :course_id, :assignments_total, :assignments_completed, "
                    ":study_items_total, :study_items_completed, :progress_percent, "
                    ":risk_status, CURRENT_TIMESTAMP) "
                    "ON CONFLICT (user_id, course_id) DO UPDATE SET "
                    "assignments_total = EXCLUDED.assignments_total, "
                    "assignments_completed = EXCLUDED.assignments_completed, "
                    "study_items_total = EXCLUDED.study_items_total, "
                    "study_items_completed = EXCLUDED.study_items_completed, "
                    "progress_percent = EXCLUDED.progress_percent, "
                    "risk_status = EXCLUDED.risk_status, "
                    "last_calculated_at = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP"
                ),
                {
                    "user_id": user_id,
                    "course_id": course_id,
                    "assignments_total": assignments_total,
                    "assignments_completed": assignments_completed,
                    "study_items_total": study_items_total,
                    "study_items_completed": study_items_completed,
                    "progress_percent": progress_percent,
                    "risk_status": risk_status,
                },
            )

    async def list_course_progress(self, user_id: str) -> list[dict[str, Any]]:
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT cp.course_id, c.name AS course_name, cp.assignments_total, "
                    "cp.assignments_completed, cp.study_items_total, cp.study_items_completed, "
                    "cp.progress_percent, cp.risk_status, cp.last_calculated_at "
                    "FROM course_progress cp JOIN courses c ON c.id = cp.course_id "
                    "WHERE cp.user_id = :user_id ORDER BY c.created_at"
                ),
                {"user_id": user_id},
            )
            rows = result.mappings().all()
        return [dict(row) for row in rows]

    async def overview_counts(self, user_id: str) -> dict[str, Any]:
        """Dashboard header counts: deadlines, overdue, upcoming exams, documents."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT "
                    "(SELECT count(*) FROM courses WHERE user_id = :user_id AND status = 'active') AS active_courses, "
                    "(SELECT count(*) FROM assignments a JOIN courses c ON c.id = a.course_id "
                    "  WHERE c.user_id = :user_id AND a.status <> 'completed' "
                    "  AND a.due_at >= CURRENT_TIMESTAMP "
                    "  AND a.due_at < CURRENT_TIMESTAMP + interval '7 days') AS deadlines_7d, "
                    "(SELECT count(*) FROM assignments a JOIN courses c ON c.id = a.course_id "
                    "  WHERE c.user_id = :user_id AND a.status <> 'completed' "
                    "  AND a.due_at < CURRENT_TIMESTAMP) AS overdue_assignments, "
                    "(SELECT count(*) FROM exams e JOIN courses c ON c.id = e.course_id "
                    "  WHERE c.user_id = :user_id AND e.exam_at >= CURRENT_TIMESTAMP "
                    "  AND e.exam_at < CURRENT_TIMESTAMP + interval '7 days') AS exams_7d, "
                    "(SELECT count(*) FROM documents WHERE user_id = :user_id AND status = 'embedded') AS embedded_documents"
                ),
                {"user_id": user_id},
            )
            row = result.mappings().one()
        return {key: int(value) for key, value in dict(row).items()}
