"""Unit tests for TASK-M5-07: progress dashboard & analytics (US 2.17/2.18)."""

from typing import Any

import pytest

from app.repositories.progress_repo import ProgressRepository
from app.services.progress_service import ProgressService

USER_ID = "dsh1dsh1-dsh1-dsh1-dsh1-dsh1dsh1dsh1"
COURSE_A = "dsh2dsh2-dsh2-dsh2-dsh2-dsh2dsh2dsh2"
COURSE_B = "dsh3dsh3-dsh3-dsh3-dsh3-dsh3dsh3dsh3"


class FakeProgressRepository(ProgressRepository):
    def __init__(self, session: Any = None) -> None:
        self.assignment_rows = [
            {"course_id": COURSE_A, "course_name": "ML", "total": 4, "completed": 3},
            {"course_id": COURSE_B, "course_name": "Calc", "total": 2, "completed": 0},
        ]
        self.study_items: dict[str, tuple[int, int]] = {COURSE_A: (2, 1)}
        self.overdue = 0
        self.upserts: list[dict[str, Any]] = []

    async def assignment_counts_by_course(self, user_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.assignment_rows]

    async def study_item_counts_by_course(self, user_id: str) -> dict[str, tuple[int, int]]:
        return dict(self.study_items)

    async def upsert_course_progress(self, **kwargs: Any) -> None:
        self.upserts.append(kwargs)

    async def list_course_progress(self, user_id: str) -> list[dict[str, Any]]:
        return [
            {
                "course_id": u["course_id"],
                "progress_percent": u["progress_percent"],
                "risk_status": u["risk_status"],
            }
            for u in self.upserts
        ]

    async def overview_counts(self, user_id: str) -> dict[str, Any]:
        return {
            "active_courses": 2,
            "deadlines_7d": 3,
            "overdue_assignments": self.overdue,
            "exams_7d": 1,
            "embedded_documents": 5,
        }


def make_service(repo: FakeProgressRepository | None = None) -> tuple[ProgressService, FakeProgressRepository]:
    repo = repo or FakeProgressRepository()
    return ProgressService(progress_repo=repo), repo


@pytest.mark.asyncio
async def test_progress_persists_per_course_with_weighted_percent() -> None:
    """Course A: (3+1)/(4+2) = 66.67% -> on_track."""
    service, repo = make_service()

    rows = await service.refresh_and_list_course_progress(USER_ID)

    upsert_a = next(u for u in repo.upserts if u["course_id"] == COURSE_A)
    assert upsert_a["assignments_total"] == 4
    assert upsert_a["study_items_total"] == 2
    assert float(upsert_a["progress_percent"]) == pytest.approx(66.67, abs=0.01)
    assert upsert_a["risk_status"] == "on_track"
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_overdue_assignments_mark_course_overdue() -> None:
    repo = FakeProgressRepository()
    repo.overdue = 2
    service, _ = make_service(repo)

    await service.refresh_and_list_course_progress(USER_ID)

    assert all(u["risk_status"] == "overdue" for u in repo.upserts)


@pytest.mark.asyncio
async def test_low_completion_with_open_work_is_at_risk() -> None:
    service, repo = make_service()

    await service.refresh_and_list_course_progress(USER_ID)

    upsert_b = next(u for u in repo.upserts if u["course_id"] == COURSE_B)
    assert float(upsert_b["progress_percent"]) == 0.0
    assert upsert_b["risk_status"] == "at_risk"


@pytest.mark.asyncio
async def test_overview_counts_shape() -> None:
    service, _ = make_service()

    overview = await service.get_overview(USER_ID)

    assert overview["active_courses"] == 2
    assert overview["deadlines_7d"] == 3
    assert overview["exams_7d"] == 1
    assert overview["embedded_documents"] == 5
    assert overview["has_overdue"] is False


@pytest.mark.asyncio
async def test_overdue_flag_reflects_state() -> None:
    repo = FakeProgressRepository()
    repo.overdue = 1
    service, _ = make_service(repo)

    overview = await service.get_overview(USER_ID)

    assert overview["has_overdue"] is True


@pytest.mark.asyncio
async def test_course_without_any_work_is_on_track() -> None:
    repo = FakeProgressRepository()
    repo.assignment_rows = [{"course_id": COURSE_A, "course_name": "Empty", "total": 0, "completed": 0}]
    repo.study_items = {}
    service, _ = make_service(repo)

    await service.refresh_and_list_course_progress(USER_ID)

    assert repo.upserts[0]["progress_percent"] == 0.0
    assert repo.upserts[0]["risk_status"] == "on_track"  # nothing due yet
