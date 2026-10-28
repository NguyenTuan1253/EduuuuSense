"""Consolidated academic calendar (US 2.9): classes + exams + deadlines.

Expands weekly recurring schedules into concrete occurrences inside the
requested range and merges exams and assignment deadlines into one list
ordered by date/time. Each item is typed so the client can color/icon
distinguish them (class / exam / assignment_deadline).
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.repositories.calendar_repo import CalendarRepository

# Proposed default pending PO approval: a single request may expand at most 8
# weeks (56 days) of recurring classes, protecting the API from unbounded ranges.
MAX_RANGE_DAYS = 56


class InvalidCalendarRangeError(Exception):
    """Raised when the requested date range is invalid or too large."""


class CalendarService:
    """Aggregated calendar use cases for the authenticated owner."""

    def __init__(self, calendar_repo: CalendarRepository) -> None:
        self._calendar_repo = calendar_repo

    @classmethod
    def from_session(cls, session: Any) -> "CalendarService":
        """Compose the service with its repository over one session."""
        return cls(calendar_repo=CalendarRepository(session))

    async def get_calendar(self, user_id: str, start: date, end: date) -> dict[str, Any]:
        """Return typed, merged calendar items for the inclusive date range."""
        if end < start:
            raise InvalidCalendarRangeError("end date must be on or after start date.")
        if (end - start).days + 1 > MAX_RANGE_DAYS:
            raise InvalidCalendarRangeError(
                f"Date range exceeds the maximum of {MAX_RANGE_DAYS} days."
            )

        schedules, exams, deadlines = await self._fetch_all(user_id, start, end)
        items: list[dict[str, Any]] = []

        for schedule in schedules:
            items.extend(self._expand_schedule(schedule, start, end))
        for exam in exams:
            items.append(self._exam_item(exam))
        for deadline in deadlines:
            items.append(self._deadline_item(deadline))

        items.sort(key=lambda item: (item["date"], item.get("start_time") or "00:00:00"))
        return {"range": {"start": start.isoformat(), "end": end.isoformat()}, "items": items}

    async def _fetch_all(
        self, user_id: str, start: date, end: date
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        start_iso, end_iso = start.isoformat(), end.isoformat()
        schedules = await self._calendar_repo.list_schedules_in_range(user_id, start_iso, end_iso)
        exams = await self._calendar_repo.list_exams_in_range(user_id, start_iso, end_iso)
        deadlines = await self._calendar_repo.list_assignment_deadlines_in_range(
            user_id, start_iso, end_iso
        )
        return schedules, exams, deadlines

    @staticmethod
    def _expand_schedule(
        schedule: dict[str, Any], range_start: date, range_end: date
    ) -> list[dict[str, Any]]:
        """Expand one weekly slot into its occurrences within the range."""
        slot_start = max(date.fromisoformat(str(schedule["start_date"])), range_start)
        slot_end = min(date.fromisoformat(str(schedule["end_date"])), range_end)
        occurrences: list[dict[str, Any]] = []
        cursor = slot_start
        while cursor <= slot_end:
            if cursor.weekday() % 7 == int(schedule["day_of_week"]):
                occurrences.append(
                    {
                        "type": "class",
                        "date": cursor.isoformat(),
                        "schedule_id": str(schedule["id"]),
                        "course_id": str(schedule["course_id"]),
                        "course_name": schedule.get("course_name"),
                        "start_time": str(schedule["start_time"]),
                        "end_time": str(schedule["end_time"]),
                        "location": schedule.get("location"),
                    }
                )
            cursor += timedelta(days=1)
        return occurrences

    @staticmethod
    def _exam_item(exam: dict[str, Any]) -> dict[str, Any]:
        exam_at = exam["exam_at"]
        if isinstance(exam_at, str):
            parsed = datetime.fromisoformat(exam_at)
        else:
            parsed = exam_at
        return {
            "type": "exam",
            "date": parsed.astimezone(UTC).date().isoformat(),
            "exam_id": str(exam["id"]),
            "course_id": str(exam["course_id"]),
            "course_name": exam.get("course_name"),
            "title": exam["title"],
            "start_time": parsed.strftime("%H:%M:%S"),
            "duration_minutes": exam.get("duration_minutes"),
            "location": exam.get("location"),
        }

    @staticmethod
    def _deadline_item(assignment: dict[str, Any]) -> dict[str, Any]:
        due_at = assignment["due_at"]
        parsed = datetime.fromisoformat(due_at) if isinstance(due_at, str) else due_at
        return {
            "type": "assignment_deadline",
            "date": parsed.astimezone(UTC).date().isoformat(),
            "assignment_id": str(assignment["id"]),
            "course_id": str(assignment["course_id"]),
            "course_name": assignment.get("course_name"),
            "title": assignment["title"],
            "start_time": parsed.strftime("%H:%M:%S"),
            "priority": assignment.get("priority"),
            "status": assignment.get("status"),
        }
