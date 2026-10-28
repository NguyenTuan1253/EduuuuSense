"""Unit tests for TASK-M5-06: study plan item lifecycle (US 2.16)."""

from typing import Any

import pytest

from app.repositories.background_job_repo import BackgroundJobRepository
from app.repositories.study_plan_repo import StudyPlanRepository
from app.services.study_plan_service import StudyPlanService

USER_ID = "sp9-sp9-sp9-sp9-sp9sp9sp9sp9"


class FakePlanRepository(StudyPlanRepository):
    def __init__(self, session: Any = None) -> None:
        self.items: dict[str, dict[str, Any]] = {}

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


def make_service() -> tuple[StudyPlanService, FakePlanRepository]:
    repo = FakePlanRepository()
    service = StudyPlanService(
        study_plan_repo=repo,
        job_repo=BackgroundJobRepository(session=None),  # type: ignore[arg-type]
    )
    return service, repo


def seed_item(repo: FakePlanRepository) -> None:
    repo.items["item-1"] = {
        "id": "item-1",
        "study_plan_id": "plan-1",
        "status": "pending",
        "completed_at": None,
        "accepted_at": None,
        "title": "Ôn tập",
        "scheduled_start": "2026-11-03T09:00:00Z",
    }


@pytest.mark.asyncio
async def test_acceptance_stamps_accepted_at() -> None:
    service, repo = make_service()
    seed_item(repo)

    accepted = await service.set_item_status(USER_ID, "item-1", "accepted")

    assert accepted["status"] == "accepted"
    assert accepted["accepted_at"] is not None
    assert accepted["completed_at"] is None


@pytest.mark.asyncio
async def test_completion_stamps_completed_at() -> None:
    service, repo = make_service()
    seed_item(repo)

    await service.set_item_status(USER_ID, "item-1", "accepted")
    completed = await service.set_item_status(USER_ID, "item-1", "completed")

    assert completed["status"] == "completed"
    assert completed["completed_at"] is not None
    assert completed["accepted_at"] is not None


@pytest.mark.asyncio
async def test_dismissal_keeps_stamps_intact() -> None:
    service, repo = make_service()
    seed_item(repo)

    dismissed = await service.set_item_status(USER_ID, "item-1", "dismissed")

    assert dismissed["status"] == "dismissed"
    assert dismissed["completed_at"] is None
    assert dismissed["accepted_at"] is None  # never accepted


@pytest.mark.asyncio
async def test_missing_item_raises_lookup_error() -> None:
    service, _ = make_service()

    with pytest.raises(LookupError):
        await service.set_item_status(USER_ID, "item-x", "accepted")


@pytest.mark.asyncio
async def test_invalid_status_is_rejected() -> None:
    service, repo = make_service()
    seed_item(repo)

    with pytest.raises(ValueError):
        await service.set_item_status(USER_ID, "item-1", "cancelled")
