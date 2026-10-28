"""Background-job persistence: SQL for background_jobs (US 2.14/2.15/2.16).

Long AI jobs return a job_id the client can poll; results and errors are
persisted here (Redis is never the source of truth).
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class BackgroundJobRepository:
    """SQL access for background_jobs. No business logic lives here."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue(
        self, *, user_id: str, job_type: str, idempotency_key: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Create a queued job; returns the existing row when the key repeats."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "INSERT INTO background_jobs (user_id, job_type, idempotency_key, result) "
                    "VALUES (:user_id, :job_type, :idempotency_key, :payload::jsonb) "
                    "ON CONFLICT (idempotency_key) DO NOTHING "
                    "RETURNING id, user_id, job_type, status, progress, result, "
                    "error_code, error_message, queued_at"
                ),
                {
                    "user_id": user_id,
                    "job_type": job_type,
                    "idempotency_key": idempotency_key,
                    "payload": payload or {},
                },
            )
            row = result.mappings().one_or_none()
            if row is None:
                existing = await self._session.execute(
                    text(
                        "SELECT id, user_id, job_type, status, progress, result, "
                        "error_code, error_message, queued_at "
                        "FROM background_jobs WHERE idempotency_key = :idempotency_key"
                    ),
                    {"idempotency_key": idempotency_key},
                )
                row = existing.mappings().one()
        return dict(row)

    async def get_job(self, user_id: str, job_id: str) -> dict[str, Any] | None:
        """Owner-scoped job status for polling."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT id, user_id, job_type, status, progress, result, "
                    "error_code, error_message, queued_at, started_at, finished_at "
                    "FROM background_jobs WHERE id = :job_id AND user_id = :user_id"
                ),
                {"job_id": job_id, "user_id": user_id},
            )
            row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def mark_running(self, job_id: str) -> bool:
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "UPDATE background_jobs SET status = 'processing', started_at = CURRENT_TIMESTAMP, "
                    "heartbeat_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = :job_id AND status = 'queued'"
                ),
                {"job_id": job_id},
            )
            marked = int(getattr(result, "rowcount", 0) or 0) > 0
        return marked

    async def mark_succeeded(self, job_id: str, result_payload: dict[str, Any]) -> bool:
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "UPDATE background_jobs SET status = 'succeeded', progress = 100, "
                    "result = :result::jsonb, finished_at = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = :job_id"
                ),
                {"job_id": job_id, "result": result_payload},
            )
            marked = int(getattr(result, "rowcount", 0) or 0) > 0
        return marked

    async def mark_failed(self, job_id: str, error_code: str, error_message: str) -> bool:
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "UPDATE background_jobs SET status = 'failed', "
                    "error_code = :error_code, error_message = :error_message, "
                    "finished_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = :job_id"
                ),
                {"job_id": job_id, "error_code": error_code, "error_message": error_message},
            )
            marked = int(getattr(result, "rowcount", 0) or 0) > 0
        return marked
