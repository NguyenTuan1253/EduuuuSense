"""Unit tests for TASK-M3-03: Celery setup & IdempotencyGuard (Invariant 8)."""

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.worker.celery_app import celery_app
from app.worker.idempotency import (
    GuardOutcome,
    TaskIdempotencyRepository,
    run_guarded,
)

KEY = "ingest:doc-0001"
TASK_TYPE = "document_ingestion"


class FakeIdempotencyRepository(TaskIdempotencyRepository):
    """In-memory mirror of the task_idempotency state machine."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.done_calls: list[str] = []
        self.failed_calls: list[str] = []

    async def find(self, idempotency_key: str) -> dict[str, Any] | None:
        row = self.rows.get(idempotency_key)
        return dict(row) if row else None

    async def try_begin(self, idempotency_key: str, task_type: str) -> str | None:
        row = self.rows.get(idempotency_key)
        if row is None:
            self.rows[idempotency_key] = {
                "id": "row-1",
                "idempotency_key": idempotency_key,
                "task_type": task_type,
                "state": "in_progress",
                "result": None,
            }
            return "row-1"
        if row["state"] == "failed":
            row["state"] = "in_progress"
            row["result"] = None
            return row["id"]
        return None

    async def mark_done(self, idempotency_key: str, result: dict[str, Any] | None = None) -> None:
        self.rows[idempotency_key]["state"] = "done"
        self.rows[idempotency_key]["result"] = result or {}
        self.done_calls.append(idempotency_key)

    async def mark_failed(self, idempotency_key: str, error_code: str | None = None) -> None:
        self.rows[idempotency_key]["state"] = "failed"
        self.rows[idempotency_key]["result"] = {"error_code": error_code}
        self.failed_calls.append(idempotency_key)


@pytest.mark.asyncio
async def test_first_execution_acquires_runs_and_marks_done() -> None:
    repo = FakeIdempotencyRepository()
    executions: list[str] = []

    async def work() -> dict[str, Any]:
        executions.append(KEY)
        return {"chunks": 12}

    outcome = await run_guarded(repo, KEY, TASK_TYPE, work)

    assert outcome.executed is True
    assert outcome.result == {"chunks": 12}
    assert executions == [KEY]
    assert repo.rows[KEY]["state"] == "done"


@pytest.mark.asyncio
async def test_done_key_is_never_reexecuted() -> None:
    """Re-delivery after success must not run the work again (duplicate sends)."""
    repo = FakeIdempotencyRepository()
    executions: list[str] = []

    async def work() -> dict[str, Any]:
        executions.append(KEY)
        return {"chunks": 12}

    await run_guarded(repo, KEY, TASK_TYPE, work)
    second = await run_guarded(repo, KEY, TASK_TYPE, work)

    assert executions == [KEY]
    assert second.executed is False
    assert second.skipped_reason == "done"


@pytest.mark.asyncio
async def test_in_progress_key_blocks_concurrent_execution() -> None:
    """A second worker cannot claim a key another worker owns."""
    repo = FakeIdempotencyRepository()
    repo.rows[KEY] = {
        "id": "row-0",
        "idempotency_key": KEY,
        "task_type": TASK_TYPE,
        "state": "in_progress",
        "result": None,
    }

    async def work() -> None:  # pragma: no cover - must not run
        raise AssertionError("work must not run while in_progress")

    outcome = await run_guarded(repo, KEY, TASK_TYPE, work)

    assert outcome.executed is False
    assert outcome.skipped_reason == "in_progress"


@pytest.mark.asyncio
async def test_failed_key_is_retried_and_can_complete() -> None:
    """Failure marks the key failed; the next delivery re-acquires and runs."""
    repo = FakeIdempotencyRepository()
    calls: list[str] = []

    async def failing() -> dict[str, Any]:
        calls.append("fail")
        raise RuntimeError("extraction boom")

    with pytest.raises(RuntimeError):
        await run_guarded(repo, KEY, TASK_TYPE, failing)

    assert repo.rows[KEY]["state"] == "failed"
    assert repo.rows[KEY]["result"] == {"error_code": "RuntimeError"}

    async def succeeding() -> dict[str, Any]:
        calls.append("ok")
        return {"chunks": 3}

    outcome = await run_guarded(repo, KEY, TASK_TYPE, succeeding)

    assert outcome.executed is True
    assert calls == ["fail", "ok"]
    assert repo.rows[KEY]["state"] == "done"


@pytest.mark.asyncio
async def test_partial_failure_does_not_mark_done() -> None:
    """Invariant: done ONLY after successful completion."""
    repo = FakeIdempotencyRepository()
    state: dict[str, str] = {"phase": "half"}

    async def work() -> dict[str, Any]:
        if state["phase"] == "half":
            raise ValueError("interrupted mid-way")
        return {}

    with pytest.raises(ValueError):
        await run_guarded(repo, KEY, TASK_TYPE, work)

    assert repo.done_calls == []
    assert repo.failed_calls == [KEY]


def test_celery_app_uses_settings_broker_and_separate_queues() -> None:
    """Worker wiring: Redis broker from settings; task families routed."""
    assert celery_app.conf.broker_url == settings.redis_url
    assert celery_app.conf.task_serializer == "json"
    routes = celery_app.conf.task_routes
    assert routes["app.worker.tasks.documents.*"]["queue"] == "edusense_documents"
    assert routes["app.worker.tasks.ai.*"]["queue"] == "edusense_ai"
    assert routes["app.worker.tasks.emails.*"]["queue"] == "edusense_emails"
    # Invariant 8 support: late ack + single prefetch keeps retries safe.
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.worker_prefetch_multiplier == 1


@pytest.mark.asyncio
async def test_guard_outcome_shape_is_stable() -> None:
    outcome = GuardOutcome(executed=False, skipped_reason="done")
    assert outcome.executed is False
    assert outcome.skipped_reason == "done"
    assert outcome.result is None
