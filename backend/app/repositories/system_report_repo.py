"""Admin system reports persistence: storage & resource consumption (US 2.24)."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class SystemReportRepository:
    """SQL access for system-wide aggregates. No business logic here."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def storage_by_user(self, limit: int = 100) -> list[dict[str, Any]]:
        """Per-user document storage consumption."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT d.user_id, u.email, "
                    "count(*) AS document_count, "
                    "COALESCE(sum(d.file_size_bytes), 0) AS storage_bytes, "
                    "count(*) FILTER (WHERE d.status = 'embedded') AS embedded_count, "
                    "count(*) FILTER (WHERE d.status = 'failed') AS failed_count "
                    "FROM documents d JOIN users u ON u.id = d.user_id "
                    "GROUP BY d.user_id, u.email ORDER BY storage_bytes DESC LIMIT :limit"
                ),
                {"limit": limit},
            )
            rows = result.mappings().all()
        return [dict(row) for row in rows]

    async def storage_totals(self) -> dict[str, Any]:
        """System-wide storage totals."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT count(*) AS document_count, "
                    "COALESCE(sum(file_size_bytes), 0) AS storage_bytes, "
                    "count(*) FILTER (WHERE status = 'pending') AS pending_count, "
                    "count(*) FILTER (WHERE status = 'processing') AS processing_count, "
                    "count(*) FILTER (WHERE status = 'embedded') AS embedded_count, "
                    "count(*) FILTER (WHERE status = 'failed') AS failed_count "
                    "FROM documents"
                )
            )
            row = result.mappings().one()
        return {key: int(value) for key, value in dict(row).items()}

    async def resource_totals(self) -> dict[str, Any]:
        """System-wide record counts: users, courses, conversations, jobs."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT "
                    "(SELECT count(*) FROM users) AS total_users, "
                    "(SELECT count(*) FROM users WHERE is_active = FALSE) AS suspended_users, "
                    "(SELECT count(*) FROM courses) AS total_courses, "
                    "(SELECT count(*) FROM ai_conversations) AS total_conversations, "
                    "(SELECT count(*) FROM question_banks) AS total_question_banks, "
                    "(SELECT count(*) FROM background_jobs WHERE status IN ('queued', 'processing')) AS active_jobs, "
                    "(SELECT count(*) FROM background_jobs WHERE status = 'failed') AS failed_jobs"
                )
            )
            row = result.mappings().one()
        return {key: int(value) for key, value in dict(row).items()}
