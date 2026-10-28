"""Study & revision plan generation + item lifecycle (US 2.16, M5-06).

Generation is a background job like the other AI features (M5-01 pattern).
The generator spreads study items across the requested window using the
user's courses; AI output is parsed defensively and normalized to the
schema's item states. Item acceptance/dismissal updates accepted_at /
completed_at per the schema CHECKs and keeps course_progress (M5-07)
recomputable from item states.
"""

import json
from typing import Any

from app.providers.gemini_adapter import GeminiAdapter
from app.repositories.background_job_repo import BackgroundJobRepository
from app.repositories.study_plan_repo import StudyPlanRepository

VALID_ITEM_STATUSES = ("pending", "accepted", "in_progress", "completed", "dismissed")


class PlanGenerationOutputError(Exception):
    """Raised when the model returns no usable plan items."""


def parse_plan_items(text: str, course_ids: list[str], start_date: str, end_date: str) -> list[dict[str, Any]]:
    """Parse/normalize the model's item array; invalid entries dropped.

    scheduled_start must fall within the plan window; course_id (when given)
    must be one of the owner's courses.
    """
    start_idx, end_idx = text.find("["), text.rfind("]")
    if start_idx == -1 or end_idx <= start_idx:
        return []
    try:
        raw = json.loads(text[start_idx : end_idx + 1])
    except json.JSONDecodeError:
        return []

    items: list[dict[str, Any]] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title", "")).strip()
        scheduled_start = str(entry.get("scheduled_start", "")).strip()
        if not title or not scheduled_start:
            continue
        if not (start_date <= scheduled_start[:10] <= end_date):
            continue
        course_id = entry.get("course_id")
        if course_id is not None and str(course_id) not in course_ids:
            course_id = None
        items.append(
            {
                "title": title[:255],
                "description": entry.get("description"),
                "scheduled_start": scheduled_start,
                "scheduled_end": entry.get("scheduled_end"),
                "course_id": course_id,
            }
        )
    return items


class StudyPlanService:
    """Plan generation (job-based) and item lifecycle for the owner."""

    def __init__(
        self,
        study_plan_repo: StudyPlanRepository,
        job_repo: BackgroundJobRepository,
        gemini: GeminiAdapter | None = None,
        enqueue: Any | None = None,
    ) -> None:
        self._plan_repo = study_plan_repo
        self._job_repo = job_repo
        self._gemini = gemini
        self._enqueue = enqueue

    @classmethod
    def from_session(cls, session: Any) -> "StudyPlanService":
        """Compose over one session (production wiring)."""
        return cls(
            study_plan_repo=StudyPlanRepository(session),
            job_repo=BackgroundJobRepository(session),
            gemini=GeminiAdapter(),
        )

    async def request_plan(
        self,
        user_id: str,
        *,
        name: str,
        start_date: str,
        end_date: str,
        plan_code: str = "free",
    ) -> dict[str, Any]:
        """Enqueue a plan-generation job for the requested window."""
        job = await self._job_repo.enqueue(
            user_id=user_id,
            job_type="study_plan_generation",
            idempotency_key=f"study_plan:{user_id}:{start_date}:{end_date}",
        )
        if job["status"] == "queued":
            self._dispatch(str(job["id"]), user_id, name, start_date, end_date, plan_code)
        return job

    def _dispatch(self, job_id: str, user_id: str, name: str, start_date: str, end_date: str, plan_code: str) -> None:
        from app.worker.tasks.study_plan import study_plan_generation_job

        study_plan_generation_job.delay(job_id, user_id, name, start_date, end_date, plan_code)

    async def list_plans(self, user_id: str) -> list[dict[str, Any]]:
        return await self._plan_repo.list_plans(user_id)

    async def get_plan_items(self, user_id: str, plan_id: str) -> dict[str, Any]:
        plan = await self._plan_repo.get_plan(user_id, plan_id)
        if plan is None:
            raise LookupError("Study plan not found.")
        return {"plan": plan, "items": await self._plan_repo.list_plan_items(user_id, plan_id)}

    async def set_item_status(
        self, user_id: str, item_id: str, new_status: str
    ) -> dict[str, Any]:
        """Accept / start / complete / dismiss an item (M5-06)."""
        if new_status not in VALID_ITEM_STATUSES:
            raise ValueError(f"Invalid item status: {new_status}")
        updated = await self._plan_repo.update_item_status(user_id, item_id, new_status)
        if updated is None:
            raise LookupError("Study plan item not found.")
        return updated
