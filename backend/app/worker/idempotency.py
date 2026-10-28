"""IdempotencyGuard: PostgreSQL-backed task dedup (Invariant 8).

Every worker task must pass through this guard. States are exactly
``in_progress`` / ``done`` / ``failed`` (schema CHECK). A task is marked done
ONLY after successful completion so failed tasks can retry; a failed key may
be re-acquired (retry) while an in-progress key may not (another worker owns
it) and a done key is never re-executed.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class TaskAlreadyRunningError(Exception):
    """Raised when a caller insists on running a still-in-progress task."""


class TaskIdempotencyRepository:
    """SQL access for the task_idempotency table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find(self, idempotency_key: str) -> dict[str, Any] | None:
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT id, idempotency_key, task_type, state, result "
                    "FROM task_idempotency WHERE idempotency_key = :key"
                ),
                {"key": idempotency_key},
            )
            row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def try_begin(self, idempotency_key: str, task_type: str) -> str | None:
        """Atomically acquire the key.

        Returns the row id when acquired:
        - fresh key -> inserted as in_progress
        - failed key -> re-acquired for retry
        Returns None when already in_progress (another worker owns it) or done
        (already completed — nothing to do).
        """
        async with self._session.begin():
            inserted = await self._session.execute(
                text(
                    "INSERT INTO task_idempotency (idempotency_key, task_type, state) "
                    "VALUES (:key, :task_type, 'in_progress') "
                    "ON CONFLICT (idempotency_key) DO NOTHING "
                    "RETURNING id"
                ),
                {"key": idempotency_key, "task_type": task_type},
            )
            row = inserted.scalar_one_or_none()
            if row is not None:
                return str(row)

            current = await self._session.execute(
                text("SELECT state FROM task_idempotency WHERE idempotency_key = :key"),
                {"key": idempotency_key},
            )
            state = current.scalar_one_or_none()
            if state == "failed":
                retried = await self._session.execute(
                    text(
                        "UPDATE task_idempotency SET state = 'in_progress', "
                        "result = NULL, finished_at = NULL, "
                        "updated_at = CURRENT_TIMESTAMP "
                        "WHERE idempotency_key = :key AND state = 'failed' "
                        "RETURNING id"
                    ),
                    {"key": idempotency_key},
                )
                retried_row = retried.scalar_one_or_none()
                return str(retried_row) if retried_row is not None else None
        return None

    async def mark_done(self, idempotency_key: str, result: dict[str, Any] | None = None) -> None:
        """Mark the task done ONLY after successful completion."""
        async with self._session.begin():
            await self._session.execute(
                text(
                    "UPDATE task_idempotency SET state = 'done', result = :result::jsonb, "
                    "finished_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP "
                    "WHERE idempotency_key = :key"
                ),
                {"key": idempotency_key, "result": result or {}},
            )

    async def mark_failed(self, idempotency_key: str, error_code: str | None = None) -> None:
        """Mark the task failed so a later run may retry it."""
        async with self._session.begin():
            await self._session.execute(
                text(
                    "UPDATE task_idempotency SET state = 'failed', "
                    "result = :error::jsonb, finished_at = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE idempotency_key = :key"
                ),
                {"key": idempotency_key, "error": {"error_code": error_code} if error_code else {}},
            )


class GuardOutcome:
    """Result of a guarded run."""

    def __init__(
        self, executed: bool, skipped_reason: str | None = None, result: Any = None
    ) -> None:
        self.executed = executed
        self.skipped_reason = skipped_reason  # "in_progress" | "done" | None
        self.result = result


async def run_guarded(
    repo: TaskIdempotencyRepository,
    idempotency_key: str,
    task_type: str,
    work: Callable[[], Awaitable[Any]],
) -> GuardOutcome:
    """Execute ``work`` exactly once per key, recording the terminal state.

    - key in progress -> skipped (reason ``in_progress``), work not called.
    - key done -> skipped (reason ``done``), work not called.
    - work raises -> key marked failed and the exception re-raised (Celery
      retry policy applies on the next delivery).
    """
    acquired_id = await repo.try_begin(idempotency_key, task_type)
    if acquired_id is None:
        current = await repo.find(idempotency_key)
        reason = (current or {}).get("state") or "in_progress"
        return GuardOutcome(executed=False, skipped_reason=reason)

    try:
        result = await work()
    except Exception as exc:
        await repo.mark_failed(idempotency_key, type(exc).__name__)
        raise
    await repo.mark_done(idempotency_key, result if isinstance(result, dict) else None)
    return GuardOutcome(executed=True, result=result)
