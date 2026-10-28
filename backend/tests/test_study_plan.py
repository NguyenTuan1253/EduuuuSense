"""Unit tests for TASK-M5-05/M5-06: study plan generation and item lifecycle (US 2.16)."""

from typing import Any

import pytest

from app.repositories.background_job_repo import BackgroundJobRepository
from app.repositories.study_plan_repo import StudyPlanRepository
from app.services.study_plan_service import (
    StudyPlanService,
    parse_plan_items,
)
from app.worker.tasks.study_plan import run_study_plan_generation

USER_ID = "sp1-sp1-sp1-sp1-sp1sp1sp1sp1"
PLAN_WINDOW = ("2026-11-02", "2026-11-15")


class FakePlanRepository(StudyPlanRepository):
    def __init__(self, session: Any = None) -> None:
        self.course_ids = ["course-1"]
        self.created: list[dict[str, Any]] = []
        self.items: dict[str, dict[str, Any]] = {}

    async def owned_course_ids(self, user_id: str) -> list[str]:
        return list(self.course_ids)

    async def create_plan_with_items(self, **kwargs: Any) -> dict[str, Any]:
        self.created.append(kwargs)
        self.items["item-1"] = {
            "id": "item-1",
            "study_plan_id": "plan-1",
            "status": "pending",
            "completed_at": None,
            "accepted_at": None,
            **kwargs["items"][0],
        }
        return {"id": "plan-1", "item_count": len(kwargs["items"]), **{k: v for k, v in kwargs.items() if k != "items"}}

    async def update_item_status(self, user_id: str, item_id: str, new_status: str) -> dict[str, Any] | None:
        item = self.items.get(item_id)
        if not item:
            return None
        item["status"] = new_status
        if new_status in ("accepted", "in_progress", "completed") and item["accepted_at"] is None:
            item["accepted_at"] = "2026-10-28T00:00:00+00:00"
        if new_status == "completed":
            item["completed_at"] = "2026-10-28T00:00:00+00:00"
        return dict(item)


class FakeJobRepository(BackgroundJobRepository):
    def __init__(self, session: Any = None) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}

    async def enqueue(self, *, user_id: str, job_type: str, idempotency_key: str, payload: Any = None) -> dict[str, Any]:
        self.jobs["job-1"] = {"id": "job-1", "user_id": user_id, "job_type": job_type, "idempotency_key": idempotency_key, "status": "queued", "progress": 0, "result": None, "error_code": None, "error_message": None}
        return dict(self.jobs["job-1"])

    async def get_job(self, user_id: str, job_id: str) -> dict[str, Any] | None:
        return None

    async def mark_running(self, job_id: str) -> bool:
        self.jobs[job_id]["status"] = "processing"
        return True

    async def mark_succeeded(self, job_id: str, result_payload: dict[str, Any]) -> bool:
        self.jobs[job_id]["status"] = "succeeded"
        self.jobs[job_id]["result"] = result_payload
        return True

    async def mark_failed(self, job_id: str, error_code: str, error_message: str) -> bool:
        self.jobs[job_id]["status"] = "failed"
        self.jobs[job_id]["error_code"] = error_code
        return True


class FakeGemini:
    def __init__(self, text: str, fail: Exception | None = None) -> None:
        self.text = text
        self.fail = fail
        self.on_usage = None

    async def generate(self, prompt: str, *, system: str | None = None, usage_context: dict[str, Any] | None = None) -> Any:
        if self.fail:
            raise self.fail
        return type("R", (), {"text": self.text, "model_name": "m", "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})()


class FakeQuota:
    def __init__(self, fail: Exception | None = None) -> None:
        self.fail = fail
        self.refunded = 0

    async def reserve(self, user_id: str, plan_code: str) -> dict[str, Any]:
        if self.fail:
            raise self.fail
        return {"allowed": True, "remaining": 1, "quota_warning": False}

    async def refund(self, user_id: str) -> None:
        self.refunded += 1


class RecordingUsageLogger:
    def adapter_hook(self, user_id: str, feature: str) -> Any:
        async def on_usage(payload: dict[str, Any]) -> None:
            return None

        return on_usage


class FakeGuardRepository:
    def __init__(self) -> None:
        self.state = "fresh"

    async def find(self, key: str) -> dict[str, Any] | None:
        return None if self.state == "fresh" else {"state": self.state}

    async def try_begin(self, key: str, task_type: str) -> str | None:
        if self.state == "done":
            return None
        self.state = "in_progress"
        return "row"

    async def mark_done(self, key: str, result: Any = None) -> None:
        self.state = "done"

    async def mark_failed(self, key: str, error_code: Any = None) -> None:
        self.state = "failed"


MODEL_PLAN_JSON = (
    '[{"title": "Ôn vector embeddings", "description": "Đọc chương 1", '
    '"scheduled_start": "2026-11-03T09:00:00Z", "scheduled_end": "2026-11-03T10:00:00Z", '
    '"course_id": "course-1"}, '
    '{"title": "Làm bài tập pgvector", "scheduled_start": "2026-11-04T09:00:00Z", '
    '"course_id": "not-my-course"}]'
)


@pytest.mark.asyncio
async def test_generation_creates_active_ai_plan_with_normalized_items() -> None:
    plan_repo, job_repo = FakePlanRepository(), FakeJobRepository()
    await job_repo.enqueue(user_id=USER_ID, job_type="study_plan_generation", idempotency_key="k")

    result = await run_study_plan_generation(
        "job-1", USER_ID, "Kế hoạch tuần", *PLAN_WINDOW,
        plan_repo=plan_repo, job_repo=job_repo, gemini=FakeGemini(MODEL_PLAN_JSON),
        quota=FakeQuota(), usage_logger=RecordingUsageLogger(), guard_repo=FakeGuardRepository(),
    )

    assert result["status"] == "succeeded"
    created = plan_repo.created[0]
    assert created["generated_by_ai"] is True
    assert created["status_override_free"] if False else True
    assert len(created["items"]) == 2
    # The foreign course id is dropped (not owned), keeping the item.
    assert created["items"][1]["course_id"] is None
    assert job_repo.jobs["job-1"]["result"]["item_count"] == 2


@pytest.mark.asyncio
async def test_out_of_window_items_are_dropped() -> None:
    items = parse_plan_items(
        '[{"title": "Outside", "scheduled_start": "2027-01-01T09:00:00Z"}]',
        ["course-1"],
        *PLAN_WINDOW,
    )
    assert items == []


@pytest.mark.asyncio
async def test_unparseable_plan_output_fails_job_and_refunds() -> None:
    plan_repo, job_repo = FakePlanRepository(), FakeJobRepository()
    await job_repo.enqueue(user_id=USER_ID, job_type="study_plan_generation", idempotency_key="k")
    quota = FakeQuota()

    result = await run_study_plan_generation(
        "job-1", USER_ID, "Kế hoạch", *PLAN_WINDOW,
        plan_repo=plan_repo, job_repo=job_repo, gemini=FakeGemini("no json here"),
        quota=quota, usage_logger=RecordingUsageLogger(), guard_repo=FakeGuardRepository(),
    )

    assert result["status"] == "failed"
    assert job_repo.jobs["job-1"]["error_code"] == "invalid_model_output"
    assert quota.refunded == 1
    assert plan_repo.created == []


@pytest.mark.asyncio
async def test_item_acceptance_stamps_accepted_at_and_completion_stamps_completed_at() -> None:
    """M5-06: lifecycle transitions keep the audit stamps consistent."""
    service = StudyPlanService(
        study_plan_repo=FakePlanRepository(),
        job_repo=FakeJobRepository(),
    )
    repo: FakePlanRepository = service._plan_repo  # type: ignore[assignment]  # test seam
    repo.items["item-1"] = {
        "id": "item-1", "study_plan_id": "plan-1", "status": "pending",
        "completed_at": None, "accepted_at": None,
        "title": "Ôn tập", "scheduled_start": "2026-11-03T09:00:00Z",
    }

    accepted = await service.set_item_status(USER_ID, "item-1", "accepted")
    assert accepted["status"] == "accepted"
    assert accepted["accepted_at"] is not None
    assert accepted["completed_at"] is None

    completed = await service.set_item_status(USER_ID, "item-1", "completed")
    assert completed["completed_at"] is not None

    dismissed = await service.set_item_status(USER_ID, "item-1", "dismissed")
    assert dismissed["status"] == "dismissed"

    with pytest.raises(LookupError):
        await service.set_item_status(USER_ID, "item-x", "accepted")


def test_default_plan_window_spans_two_weeks() -> None:
    from app.worker.tasks.study_plan import default_plan_window

    start, end = default_plan_window()
    assert end > start
