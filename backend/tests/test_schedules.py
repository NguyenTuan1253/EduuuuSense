"""Unit tests for TASK-M2-04: weekly recurring class schedules (US 2.9)."""

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.schedules import get_schedule_service
from app.core.security import create_access_token
from app.main import app
from app.repositories.schedule_repo import ScheduleRepository
from app.services.schedule_service import ScheduleService

USER_ID = "12121212-1212-1212-1212-121212121212"
OTHER_ID = "34343434-3434-3434-3434-343434343434"
COURSE_ID = "56565656-5656-5656-5656-565656565656"
SCHEDULE_ID = "78787878-7878-7878-7878-787878787878"


class FakeScheduleRepository(ScheduleRepository):
    def __init__(self, session: AsyncSession | None = None) -> None:
        self.course_owners: dict[str, str] = {COURSE_ID: USER_ID}
        self.schedules: dict[str, dict[str, Any]] = {}
        self.deleted: list[str] = []

    def _base_row(self, **overrides: Any) -> dict[str, Any]:
        row: dict[str, Any] = {
            "id": SCHEDULE_ID,
            "course_id": COURSE_ID,
            "course_name": "ML",
            "day_of_week": 1,
            "start_time": "08:00:00",
            "end_time": "09:30:00",
            "location": "Room A",
            "start_date": "2026-11-02",
            "end_date": "2026-12-20",
            "created_at": "2026-10-28T00:00:00+00:00",
            "updated_at": "2026-10-28T00:00:00+00:00",
        }
        row.update(overrides)
        return row

    async def create_schedule(self, **kwargs: Any) -> dict[str, Any] | None:
        if self.course_owners.get(kwargs["course_id"]) != kwargs["user_id"]:
            return None
        schedule_id = f"new-{len(self.schedules) + 1}"
        self.schedules[schedule_id] = self._base_row(
            id=schedule_id,
            **{k: v for k, v in kwargs.items() if k != "user_id"},
        )
        return dict(self.schedules[schedule_id])

    async def list_schedules(self, user_id: str, course_id: str) -> list[dict[str, Any]]:
        if self.course_owners.get(course_id) != user_id:
            return []
        return [dict(s) for s in self.schedules.values() if s["course_id"] == course_id]

    async def get_schedule(self, user_id: str, schedule_id: str) -> dict[str, Any] | None:
        schedule = self.schedules.get(schedule_id)
        if schedule and self.course_owners.get(schedule["course_id"]) == user_id:
            return dict(schedule)
        return None

    async def list_day_schedules_for_owner(self, user_id: str, day_of_week: int) -> list[dict[str, Any]]:
        return [
            dict(s)
            for s in self.schedules.values()
            if self.course_owners.get(s["course_id"]) == user_id and s["day_of_week"] == day_of_week
        ]

    async def update_schedule(self, user_id: str, schedule_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
        schedule = self.schedules.get(schedule_id)
        if not schedule or self.course_owners.get(schedule["course_id"]) != user_id:
            return None
        schedule.update(changes)
        return dict(schedule)

    async def delete_schedule(self, user_id: str, schedule_id: str) -> bool:
        schedule = self.schedules.get(schedule_id)
        if not schedule or self.course_owners.get(schedule["course_id"]) != user_id:
            return False
        del self.schedules[schedule_id]
        self.deleted.append(schedule_id)
        return True


@pytest_asyncio.fixture
async def client_and_repo() -> AsyncGenerator[tuple[AsyncClient, FakeScheduleRepository], None]:
    repo = FakeScheduleRepository()

    async def override_schedule_service() -> ScheduleService:
        return ScheduleService(schedule_repo=repo)

    app.dependency_overrides[get_schedule_service] = override_schedule_service
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, repo
    app.dependency_overrides.clear()


def auth(user_id: str = USER_ID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


VALID = {
    "day_of_week": 1,
    "start_time": "10:00",
    "end_time": "11:30",
    "location": "Room B",
    "start_date": "2026-11-02",
    "end_date": "2026-12-20",
}


@pytest.mark.asyncio
async def test_create_schedule_returns_201_with_no_warnings(
    client_and_repo: tuple[AsyncClient, FakeScheduleRepository],
) -> None:
    client, repo = client_and_repo
    response = await client.post(f"/api/v1/courses/{COURSE_ID}/schedules", json=VALID, headers=auth())

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["day_of_week"] == 1
    assert response.json()["warnings"] == []
    assert repo.schedules


@pytest.mark.asyncio
async def test_overlapping_slot_returns_warning_but_still_creates(
    client_and_repo: tuple[AsyncClient, FakeScheduleRepository],
) -> None:
    """US requirement: warn on overlapping entries (non-blocking)."""
    client, repo = client_and_repo
    repo.schedules[SCHEDULE_ID] = repo._base_row()

    response = await client.post(
        f"/api/v1/courses/{COURSE_ID}/schedules",
        json={**VALID, "start_time": "09:00", "end_time": "10:30"},
        headers=auth(),
    )

    assert response.status_code == 201
    warnings = response.json()["warnings"]
    assert len(warnings) == 1
    assert warnings[0]["schedule_id"] == SCHEDULE_ID
    assert "overlaps" in warnings[0]["message"]


@pytest.mark.asyncio
async def test_adjacent_and_disjoint_slots_produce_no_warning(
    client_and_repo: tuple[AsyncClient, FakeScheduleRepository],
) -> None:
    client, repo = client_and_repo
    repo.schedules[SCHEDULE_ID] = repo._base_row()

    adjacent = await client.post(
        f"/api/v1/courses/{COURSE_ID}/schedules",
        json={**VALID, "start_time": "09:30", "end_time": "10:00"},  # starts exactly when base ends
        headers=auth(),
    )
    other_day = await client.post(
        f"/api/v1/courses/{COURSE_ID}/schedules",
        json={**VALID, "day_of_week": 3},
        headers=auth(),
    )
    other_dates = await client.post(
        f"/api/v1/courses/{COURSE_ID}/schedules",
        json={**VALID, "start_date": "2027-01-05", "end_date": "2027-03-05"},
        headers=auth(),
    )

    for response in (adjacent, other_day, other_dates):
        assert response.status_code == 201
        assert response.json()["warnings"] == []


@pytest.mark.asyncio
async def test_overlap_check_covers_other_courses_of_same_owner(
    client_and_repo: tuple[AsyncClient, FakeScheduleRepository],
) -> None:
    """The scan is per owner across ALL their courses, not just one course."""
    client, repo = client_and_repo
    other_course = "aaaaaaaa-0000-0000-0000-000000000000"
    repo.course_owners[other_course] = USER_ID
    repo.schedules[SCHEDULE_ID] = repo._base_row(
        course_id=other_course, course_name="Other Course", start_time="10:30", end_time="12:00"
    )

    response = await client.post(
        f"/api/v1/courses/{COURSE_ID}/schedules", json=VALID, headers=auth()
    )

    assert response.status_code == 201
    assert response.json()["warnings"][0]["course_id"] == other_course


@pytest.mark.asyncio
async def test_update_rechecks_overlaps_excluding_itself(
    client_and_repo: tuple[AsyncClient, FakeScheduleRepository],
) -> None:
    client, repo = client_and_repo
    repo.schedules[SCHEDULE_ID] = repo._base_row()
    repo.schedules["bbbbbbbb-0000-0000-0000-000000000000"] = repo._base_row(
        id="bbbbbbbb-0000-0000-0000-000000000000",
        day_of_week=2,
        start_time="13:00",
        end_time="14:30",
    )

    response = await client.patch(
        f"/api/v1/schedules/{SCHEDULE_ID}",
        json={"day_of_week": 2, "start_time": "14:00", "end_time": "15:00"},
        headers=auth(),
    )

    assert response.status_code == 200
    warnings = response.json()["warnings"]
    assert len(warnings) == 1
    assert warnings[0]["schedule_id"] == "bbbbbbbb-0000-0000-0000-000000000000"


@pytest.mark.asyncio
async def test_end_before_start_rejected_mirroring_schema_check(
    client_and_repo: tuple[AsyncClient, FakeScheduleRepository],
) -> None:
    client, repo = client_and_repo
    bad_time = await client.post(
        f"/api/v1/courses/{COURSE_ID}/schedules",
        json={**VALID, "start_time": "12:00", "end_time": "11:00"},
        headers=auth(),
    )
    bad_dates = await client.post(
        f"/api/v1/courses/{COURSE_ID}/schedules",
        json={**VALID, "start_date": "2026-12-20", "end_date": "2026-11-02"},
        headers=auth(),
    )
    bad_day = await client.post(
        f"/api/v1/courses/{COURSE_ID}/schedules", json={**VALID, "day_of_week": 7}, headers=auth()
    )

    assert bad_time.status_code == 422
    assert bad_dates.status_code == 422
    assert bad_day.status_code == 422
    assert repo.schedules == {}


@pytest.mark.asyncio
async def test_foreign_schedule_and_course_are_404(
    client_and_repo: tuple[AsyncClient, FakeScheduleRepository],
) -> None:
    client, repo = client_and_repo
    repo.schedules[SCHEDULE_ID] = repo._base_row()

    foreign_patch = await client.patch(
        f"/api/v1/schedules/{SCHEDULE_ID}", json={"location": "X"}, headers=auth(OTHER_ID)
    )
    foreign_course = await client.post(
        "/api/v1/courses/00000000-0000-0000-0000-000000000000/schedules", json=VALID, headers=auth()
    )

    assert foreign_patch.status_code == 404
    assert foreign_course.status_code == 404


@pytest.mark.asyncio
async def test_delete_schedule(
    client_and_repo: tuple[AsyncClient, FakeScheduleRepository],
) -> None:
    client, repo = client_and_repo
    repo.schedules[SCHEDULE_ID] = repo._base_row()

    response = await client.delete(f"/api/v1/schedules/{SCHEDULE_ID}", headers=auth())

    assert response.status_code == 200
    assert repo.deleted == [SCHEDULE_ID]
