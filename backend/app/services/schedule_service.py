"""Weekly recurring class schedule logic (US 2.9) with overlap warnings.

Source-of-Truth requirement (Manage class schedule): "Warn on overlapping
entries." Overlaps are non-blocking warnings — the create/update succeeds and
the response carries a ``warnings`` list describing each conflicting slot
(same owner, same weekday, overlapping time, overlapping date range).
"""

from typing import Any

from app.repositories.schedule_repo import ScheduleRepository


class ScheduleNotFoundError(Exception):
    """Raised when the schedule slot (or its course) is not the owner's."""


def _overlaps(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """True when two slots on the same weekday overlap in time AND dates."""
    if a["day_of_week"] != b["day_of_week"]:
        return False
    time_overlap = a["start_time"] < b["end_time"] and b["start_time"] < a["end_time"]
    date_overlap = a["start_date"] <= b["end_date"] and b["start_date"] <= a["end_date"]
    return bool(time_overlap and date_overlap)


class ScheduleService:
    """Weekly schedule use cases for the authenticated owner."""

    def __init__(self, schedule_repo: ScheduleRepository) -> None:
        self._schedule_repo = schedule_repo

    @classmethod
    def from_session(cls, session: Any) -> "ScheduleService":
        """Compose the service with its repository over one session."""
        return cls(schedule_repo=ScheduleRepository(session))

    async def find_overlaps(
        self, user_id: str, candidate: dict[str, Any], exclude_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Describe owned slots the candidate overlaps with (warning payload)."""
        same_day = await self._schedule_repo.list_day_schedules_for_owner(
            user_id, int(candidate["day_of_week"])
        )
        warnings: list[dict[str, Any]] = []
        for slot in same_day:
            if exclude_id is not None and str(slot["id"]) == exclude_id:
                continue
            if _overlaps(candidate, slot):
                warnings.append(
                    {
                        "schedule_id": str(slot["id"]),
                        "course_id": str(slot["course_id"]),
                        "course_name": slot.get("course_name"),
                        "day_of_week": slot["day_of_week"],
                        "start_time": str(slot["start_time"]),
                        "end_time": str(slot["end_time"]),
                        "message": "This class overlaps another scheduled class of yours.",
                    }
                )
        return warnings

    async def create_schedule(
        self, user_id: str, course_id: str, data: dict[str, Any]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Create a weekly slot; returns (schedule, overlap warnings)."""
        created = await self._schedule_repo.create_schedule(
            user_id=user_id, course_id=course_id, **data
        )
        if created is None:
            raise ScheduleNotFoundError("Course not found.")
        return created, await self.find_overlaps(user_id, created, exclude_id=str(created["id"]))

    async def list_schedules(self, user_id: str, course_id: str) -> list[dict[str, Any]]:
        return await self._schedule_repo.list_schedules(user_id, course_id)

    async def get_schedule(self, user_id: str, schedule_id: str) -> dict[str, Any]:
        schedule = await self._schedule_repo.get_schedule(user_id, schedule_id)
        if schedule is None:
            raise ScheduleNotFoundError("Schedule not found.")
        return schedule

    async def update_schedule(
        self, user_id: str, schedule_id: str, changes: dict[str, Any]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Update a slot; re-checks overlaps against the updated candidate."""
        await self.get_schedule(user_id, schedule_id)
        updated = await self._schedule_repo.update_schedule(user_id, schedule_id, changes)
        if updated is None:
            raise ScheduleNotFoundError("Schedule not found.")
        return updated, await self.find_overlaps(user_id, updated, exclude_id=schedule_id)

    async def delete_schedule(self, user_id: str, schedule_id: str) -> dict[str, Any]:
        deleted = await self._schedule_repo.delete_schedule(user_id, schedule_id)
        if not deleted:
            raise ScheduleNotFoundError("Schedule not found.")
        return {"deleted": True}
