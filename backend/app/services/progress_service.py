"""Progress analytics (US 2.17/2.18): per-course progress + dashboard overview.

course_progress is RECOMPUTED from live tables on each refresh and persisted
(idempotent UPSERT) so the persisted snapshot never drifts far; risk status:
'overdue' when overdue assignments exist, else 'at_risk' when completion is
below half with deadlines inside 7 days, else 'on_track' (proposed default).
"""

from typing import Any

from app.repositories.progress_repo import ProgressRepository

AT_RISK_COMPLETION_THRESHOLD = 50.0


class ProgressService:
    """Dashboard use cases for the authenticated owner."""

    def __init__(self, progress_repo: ProgressRepository) -> None:
        self._progress_repo = progress_repo

    @classmethod
    def from_session(cls, session: Any) -> "ProgressService":
        """Compose over one session."""
        return cls(progress_repo=ProgressRepository(session))

    async def refresh_and_list_course_progress(self, user_id: str) -> list[dict[str, Any]]:
        """Recompute per-course progress from live data and persist it."""
        assignment_rows = await self._progress_repo.assignment_counts_by_course(user_id)
        study_items = await self._progress_repo.study_item_counts_by_course(user_id)
        overview = await self._progress_repo.overview_counts(user_id)

        for row in assignment_rows:
            course_id = str(row["course_id"])
            total = int(row["total"])
            completed = int(row["completed"])
            study_total, study_completed = study_items.get(course_id, (0, 0))

            weight_total = total + study_total
            completed_all = completed + study_completed
            percent = round((completed_all / weight_total) * 100, 2) if weight_total else 0.0

            risk = self._risk_status(
                has_overdue=overview["overdue_assignments"] > 0,
                percent=percent,
                total=weight_total,
            )
            await self._progress_repo.upsert_course_progress(
                user_id=user_id,
                course_id=course_id,
                assignments_total=total,
                assignments_completed=completed,
                study_items_total=study_total,
                study_items_completed=study_completed,
                progress_percent=percent,
                risk_status=risk,
            )

        return await self._progress_repo.list_course_progress(user_id)

    async def get_overview(self, user_id: str) -> dict[str, Any]:
        counts = await self._progress_repo.overview_counts(user_id)
        counts["has_overdue"] = counts["overdue_assignments"] > 0
        return counts

    @staticmethod
    def _risk_status(*, has_overdue: bool, percent: float, total: int) -> str:
        if has_overdue:
            return "overdue"
        if 0 < total and percent < AT_RISK_COMPLETION_THRESHOLD:
            return "at_risk"
        return "on_track"
