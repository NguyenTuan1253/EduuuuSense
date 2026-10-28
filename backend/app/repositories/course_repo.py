"""Course persistence: SQL access for the courses aggregate (US 2.6/Manage courses).

Every query is scoped by ``user_id`` — multi-tenant isolation is enforced in
the SQL itself, never in post-filtering.
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class CourseRepository:
    """SQL access for courses. No business logic lives here."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_course(
        self,
        *,
        user_id: str,
        name: str,
        description: str | None,
        course_code: str | None,
        instructor: str | None,
        semester: str | None,
        academic_year: str | None,
    ) -> dict[str, Any]:
        """Insert one course owned by ``user_id``."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "INSERT INTO courses "
                    "(user_id, course_code, name, description, instructor, semester, academic_year) "
                    "VALUES (:user_id, :course_code, :name, :description, :instructor, "
                    ":semester, :academic_year) "
                    "RETURNING id, user_id, course_code, name, description, instructor, "
                    "semester, academic_year, status, created_at, updated_at"
                ),
                {
                    "user_id": user_id,
                    "course_code": course_code,
                    "name": name,
                    "description": description,
                    "instructor": instructor,
                    "semester": semester,
                    "academic_year": academic_year,
                },
            )
            row = result.mappings().one()
        return dict(row)

    async def list_courses(
        self, user_id: str, status: str | None = None
    ) -> list[dict[str, Any]]:
        """List the owner's courses, newest first, optionally by status."""
        params: dict[str, Any] = {"user_id": user_id}
        status_filter = ""
        if status is not None:
            status_filter = " AND status = :status"
            params["status"] = status
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT id, user_id, course_code, name, description, instructor, "
                    "semester, academic_year, status, created_at, updated_at "
                    f"FROM courses WHERE user_id = :user_id{status_filter} "
                    "ORDER BY created_at DESC"
                ),
                params,
            )
            rows = result.mappings().all()
        return [dict(row) for row in rows]

    async def get_course_by_natural_key(
        self,
        *,
        user_id: str,
        course_code: str | None,
        semester: str | None,
        academic_year: str | None,
    ) -> dict[str, Any] | None:
        """Find a course matching the uniqueness key (user, code, semester, year).

        Mirrors the PostgreSQL UNIQUE constraint semantics: NULLs are distinct,
        so a conflict is only possible when course_code, semester, and
        academic_year are all provided.
        """
        if course_code is None or semester is None or academic_year is None:
            return None
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT id, user_id, course_code, name, description, instructor, "
                    "semester, academic_year, status, created_at, updated_at "
                    "FROM courses "
                    "WHERE user_id = :user_id "
                    "AND course_code = :course_code "
                    "AND semester = :semester "
                    "AND academic_year = :academic_year "
                    "LIMIT 1"
                ),
                {
                    "user_id": user_id,
                    "course_code": course_code,
                    "semester": semester,
                    "academic_year": academic_year,
                },
            )
            row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def get_course(self, user_id: str, course_id: str) -> dict[str, Any] | None:
        """Fetch one course owned by ``user_id`` (None when missing/not owner)."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT id, user_id, course_code, name, description, instructor, "
                    "semester, academic_year, status, created_at, updated_at "
                    "FROM courses WHERE id = :course_id AND user_id = :user_id"
                ),
                {"course_id": course_id, "user_id": user_id},
            )
            row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def update_course(
        self, user_id: str, course_id: str, changes: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Apply an explicit whitelist of changes to an owned course."""
        allowed = (
            "course_code",
            "name",
            "description",
            "instructor",
            "semester",
            "academic_year",
            "status",
        )
        safe_changes = {k: v for k, v in changes.items() if k in allowed}
        if not safe_changes:
            return await self.get_course(user_id, course_id)

        set_clause = ", ".join(f"{column} = :{column}" for column in safe_changes)
        params: dict[str, Any] = {**safe_changes, "course_id": course_id, "user_id": user_id}
        async with self._session.begin():
            await self._session.execute(
                text(
                    f"UPDATE courses SET {set_clause}, "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = :course_id AND user_id = :user_id"
                ),
                params,
            )
        return await self.get_course(user_id, course_id)

    async def delete_course_with_counts(
        self, user_id: str, course_id: str
    ) -> dict[str, Any] | None:
        """Delete an owned course after counting linked records (US: delete must
        state the effect on linked assignments/exams/documents).

        Returns the pre-delete linked counts, or None when the course does not
        belong to ``user_id``. Deletion cascades via foreign keys.
        """
        async with self._session.begin():
            exists = await self._session.execute(
                text("SELECT 1 FROM courses WHERE id = :course_id AND user_id = :user_id"),
                {"course_id": course_id, "user_id": user_id},
            )
            if exists.scalar_one_or_none() is None:
                return None

            counts: dict[str, int] = {}
            for table in ("assignments", "exams", "class_schedules", "documents"):
                count_result = await self._session.execute(
                    text(f"SELECT count(*) FROM {table} WHERE course_id = :course_id"),
                    {"course_id": course_id},
                )
                counts[table] = int(count_result.scalar_one() or 0)

            await self._session.execute(
                text("DELETE FROM courses WHERE id = :course_id AND user_id = :user_id"),
                {"course_id": course_id, "user_id": user_id},
            )
        return counts
