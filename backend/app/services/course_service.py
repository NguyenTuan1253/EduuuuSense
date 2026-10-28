"""Course business logic (US: Manage courses)."""

from typing import Any

from app.repositories.course_repo import CourseRepository


class CourseNotFoundError(Exception):
    """Raised when a course does not exist for the requesting owner."""


class DuplicateCourseError(Exception):
    """Raised when the (user, code, semester, year) uniqueness would be violated."""


class CourseService:
    """Course use cases for the authenticated owner."""

    def __init__(self, course_repo: CourseRepository) -> None:
        self._course_repo = course_repo

    @classmethod
    def from_session(cls, session: Any) -> "CourseService":
        """Compose the service with its repository over one session."""
        return cls(course_repo=CourseRepository(session))

    async def create_course(self, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Create a course; duplicate (code, semester, year) is rejected."""
        duplicate = await self._course_repo.get_course_by_natural_key(
            user_id=user_id,
            course_code=data.get("course_code"),
            semester=data.get("semester"),
            academic_year=data.get("academic_year"),
        )
        if duplicate is not None:
            raise DuplicateCourseError(
                "A course with the same code, semester, and academic year already exists."
            )
        return await self._course_repo.create_course(user_id=user_id, **data)

    async def get_course(self, user_id: str, course_id: str) -> dict[str, Any]:
        course = await self._course_repo.get_course(user_id, course_id)
        if course is None:
            raise CourseNotFoundError("Course not found.")
        return course

    async def list_courses(self, user_id: str, status: str | None = None) -> list[dict[str, Any]]:
        return await self._course_repo.list_courses(user_id, status)

    async def update_course(
        self, user_id: str, course_id: str, changes: dict[str, Any]
    ) -> dict[str, Any]:
        """Apply provided changes; re-checks the uniqueness key when altered."""
        current = await self.get_course(user_id, course_id)
        merged = {**current, **{k: v for k, v in changes.items() if v is not None or k in changes}}
        if any(k in changes for k in ("course_code", "semester", "academic_year")):
            duplicate = await self._course_repo.get_course_by_natural_key(
                user_id=user_id,
                course_code=merged.get("course_code"),
                semester=merged.get("semester"),
                academic_year=merged.get("academic_year"),
            )
            if duplicate is not None and duplicate["id"] != current["id"]:
                raise DuplicateCourseError(
                    "A course with the same code, semester, and academic year already exists."
                )
        updated = await self._course_repo.update_course(user_id, course_id, changes)
        if updated is None:
            raise CourseNotFoundError("Course not found.")
        return updated

    async def delete_course(self, user_id: str, course_id: str) -> dict[str, Any]:
        """Delete and report the effect on linked records (US requirement)."""
        counts = await self._course_repo.delete_course_with_counts(user_id, course_id)
        if counts is None:
            raise CourseNotFoundError("Course not found.")
        return {"deleted": True, "linked_counts": counts}
