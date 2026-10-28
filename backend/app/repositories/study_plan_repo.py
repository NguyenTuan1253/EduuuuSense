"""Study-plan persistence: SQL for study_plans and study_plan_items (US 2.16)."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class StudyPlanRepository:
    """SQL access for the study-plan aggregate. No business logic lives here."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def owned_course_ids(self, user_id: str) -> list[str]:
        async with self._session.begin():
            result = await self._session.execute(
                text("SELECT id FROM courses WHERE user_id = :user_id"),
                {"user_id": user_id},
            )
            ids = [str(r[0]) for r in result.all()]
        return ids

    async def create_plan_with_items(
        self,
        *,
        user_id: str,
        name: str,
        description: str | None,
        start_date: str,
        end_date: str,
        generated_by_ai: bool,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Atomically create the plan and its scheduled items."""
        async with self._session.begin():
            plan_result = await self._session.execute(
                text(
                    "INSERT INTO study_plans (user_id, name, description, start_date, end_date, "
                    "generated_by_ai, status) "
                    "VALUES (:user_id, :name, :description, :start_date, :end_date, "
                    ":generated_by_ai, 'active') "
                    "RETURNING id, user_id, name, description, start_date, end_date, status, "
                    "generated_by_ai, created_at"
                ),
                {
                    "user_id": user_id,
                    "name": name,
                    "description": description,
                    "start_date": start_date,
                    "end_date": end_date,
                    "generated_by_ai": generated_by_ai,
                },
            )
            plan = dict(plan_result.mappings().one())

            for order, item in enumerate(items):
                await self._session.execute(
                    text(
                        "INSERT INTO study_plan_items (study_plan_id, course_id, title, "
                        "description, scheduled_start, scheduled_end, display_order) "
                        "VALUES (:plan_id, :course_id, :title, :description, "
                        ":scheduled_start, :scheduled_end, :display_order)"
                    ),
                    {
                        "plan_id": plan["id"],
                        "course_id": item.get("course_id"),
                        "title": item["title"],
                        "description": item.get("description"),
                        "scheduled_start": item["scheduled_start"],
                        "scheduled_end": item.get("scheduled_end"),
                        "display_order": order,
                    },
                )
        plan["item_count"] = len(items)
        return plan

    async def list_plans(self, user_id: str) -> list[dict[str, Any]]:
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT id, user_id, name, description, start_date, end_date, status, "
                    "generated_by_ai, created_at FROM study_plans "
                    "WHERE user_id = :user_id ORDER BY created_at DESC"
                ),
                {"user_id": user_id},
            )
            rows = result.mappings().all()
        return [dict(row) for row in rows]

    async def get_plan(self, user_id: str, plan_id: str) -> dict[str, Any] | None:
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT id, user_id, name, description, start_date, end_date, status, "
                    "generated_by_ai, created_at FROM study_plans "
                    "WHERE id = :plan_id AND user_id = :user_id"
                ),
                {"plan_id": plan_id, "user_id": user_id},
            )
            row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def list_plan_items(self, user_id: str, plan_id: str) -> list[dict[str, Any]]:
        """Owner-scoped items ordered by schedule."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT i.id, i.study_plan_id, i.course_id, i.title, i.description, "
                    "i.scheduled_start, i.scheduled_end, i.status, i.completed_at, "
                    "i.accepted_at, i.display_order "
                    "FROM study_plan_items i JOIN study_plans p ON p.id = i.study_plan_id "
                    "WHERE i.study_plan_id = :plan_id AND p.user_id = :user_id "
                    "ORDER BY i.scheduled_start ASC, i.display_order ASC"
                ),
                {"plan_id": plan_id, "user_id": user_id},
            )
            rows = result.mappings().all()
        return [dict(row) for row in rows]

    async def update_item_status(
        self, user_id: str, item_id: str, new_status: str
    ) -> dict[str, Any] | None:
        """Transition an item's status with accepted_at/completed_at bookkeeping."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "UPDATE study_plan_items i SET "
                    "status = :new_status, "
                    "accepted_at = CASE WHEN :new_status IN ('accepted', 'in_progress', 'completed') "
                    "  AND i.accepted_at IS NULL THEN CURRENT_TIMESTAMP ELSE i.accepted_at END, "
                    "completed_at = CASE WHEN :new_status = 'completed' "
                    "  THEN CURRENT_TIMESTAMP ELSE i.completed_at END, "
                    "updated_at = CURRENT_TIMESTAMP "
                    "FROM study_plans p "
                    "WHERE i.study_plan_id = p.id AND i.id = :item_id AND p.user_id = :user_id "
                    "RETURNING i.id, i.study_plan_id, i.status, i.completed_at, i.accepted_at"
                ),
                {"item_id": item_id, "new_status": new_status, "user_id": user_id},
            )
            row = result.mappings().one_or_none()
        return dict(row) if row else None
