"""Exam business logic (US 2.8): subject, date/time, location, edit/delete."""

from datetime import UTC, datetime, timedelta
from typing import Any

from app.repositories.exam_repo import ExamRepository


class ExamNotFoundError(Exception):
    """Raised when the exam (or its owning course) is not the owner's."""


class ExamService:
    """Exam use cases for the authenticated owner."""

    def __init__(self, exam_repo: ExamRepository) -> None:
        self._exam_repo = exam_repo

    @classmethod
    def from_session(cls, session: Any) -> "ExamService":
        """Compose the service with its repository over one session."""
        return cls(exam_repo=ExamRepository(session))

    async def create_exam(self, user_id: str, course_id: str, data: dict[str, Any]) -> dict[str, Any]:
        created = await self._exam_repo.create_exam(user_id=user_id, course_id=course_id, **data)
        if created is None:
            raise ExamNotFoundError("Course not found.")
        return created

    async def list_exams(self, user_id: str, course_id: str) -> list[dict[str, Any]]:
        return await self._exam_repo.list_exams(user_id, course_id)

    async def get_exam(self, user_id: str, exam_id: str) -> dict[str, Any]:
        exam = await self._exam_repo.get_exam(user_id, exam_id)
        if exam is None:
            raise ExamNotFoundError("Exam not found.")
        return exam

    async def update_exam(self, user_id: str, exam_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        updated = await self._exam_repo.update_exam(user_id, exam_id, changes)
        if updated is None:
            raise ExamNotFoundError("Exam not found.")
        return updated

    async def delete_exam(self, user_id: str, exam_id: str) -> dict[str, Any]:
        deleted = await self._exam_repo.delete_exam(user_id, exam_id)
        if not deleted:
            raise ExamNotFoundError("Exam not found.")
        return {"deleted": True}


def is_within_reminder_window(exam_at: datetime, days_before: int, now: datetime | None = None) -> bool:
    """Shared helper for reminder milestones: True when the exam is within
    ``days_before`` days from ``now`` (used later by M5-08 reminder scans)."""
    reference = now or datetime.now(tz=exam_at.tzinfo or UTC)
    return timedelta(0) <= (exam_at - reference) <= timedelta(days=days_before)
