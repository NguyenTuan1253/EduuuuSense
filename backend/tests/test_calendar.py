"""Unit tests for TASK-M2-05: consolidated academic calendar (US 2.9)."""

from collections.abc import AsyncGenerator
from datetime import date
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.calendar import get_calendar_service
from app.core.security import create_access_token
from app.main import app
from app.repositories.calendar_repo import CalendarRepository
from app.services.calendar_service import CalendarService

USER_ID = "e1e1e1e1-e1e1-e1e1-e1e1-e1e1e1e1e1e1"
OTHER_ID = "f2f2f2f2-f2f2-f2f2-f2f2-f2f2f2f2f2f2"
COURSE_ID = "a3a3a3a3-a3a3-a3a3-a3a3-a3a3a3a3a3a3"

# 2026-11-02 is a Monday.
WEEK_START = date(2026, 11, 2)


class FakeCalendarRepository(CalendarRepository):
    def __init__(self, session: AsyncSession | None = None, *, user_id: str = USER_ID) -> None:
        self.user_id = user_id
        self.schedules: list[dict[str, Any]] = []
        self.exams: list[dict[str, Any]] = []
        self.deadlines: list[dict[str, Any]] = []

    async def list_schedules_in_range(self, user_id: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
        return [dict(s) for s in self.schedules] if user_id == self.user_id else []

    async def list_exams_in_range(self, user_id: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
        return [dict(e) for e in self.exams] if user_id == self.user_id else []

    async def list_assignment_deadlines_in_range(self, user_id: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
        return [dict(a) for a in self.deadlines] if user_id == self.user_id else []


@pytest_asyncio.fixture
async def client_and_repo() -> AsyncGenerator[tuple[AsyncClient, FakeCalendarRepository], None]:
    repo = FakeCalendarRepository()

    async def override_calendar_service() -> CalendarService:
        return CalendarService(calendar_repo=repo)

    app.dependency_overrides[get_calendar_service] = override_calendar_service
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, repo
    app.dependency_overrides.clear()


def auth(user_id: str = USER_ID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def seeded_repo() -> FakeCalendarRepository:
    repo = FakeCalendarRepository()
    repo.schedules.append(
        {
            "id": "sch-1",
            "course_id": COURSE_ID,
            "course_name": "Machine Learning",
            "day_of_week": 0,  # Monday
            "start_time": "08:00:00",
            "end_time": "09:30:00",
            "location": "Room A",
            "start_date": "2026-11-01",
            "end_date": "2026-12-20",
        }
    )
    repo.exams.append(
        {
            "id": "exam-1",
            "course_id": COURSE_ID,
            "course_name": "Machine Learning",
            "title": "Final Exam",
            "exam_at": "2026-11-06T07:30:00+07:00",  # Friday
            "duration_minutes": 90,
            "location": "Hall B",
        }
    )
    repo.deadlines.append(
        {
            "id": "asg-1",
            "course_id": COURSE_ID,
            "course_name": "Machine Learning",
            "title": "HW1",
            "due_at": "2026-11-06T23:59:00+07:00",  # Friday
            "priority": "high",
            "status": "pending",
        }
    )
    return repo


@pytest.mark.asyncio
async def test_calendar_merges_classes_exams_and_deadlines_typed(
    client_and_repo: tuple[AsyncClient, FakeCalendarRepository],
) -> None:
    """US 2.9: one merged list; items distinguishable by type."""
    client, repo = client_and_repo
    seeded = seeded_repo()
    repo.schedules, repo.exams, repo.deadlines = seeded.schedules, seeded.exams, seeded.deadlines

    response = await client.get(
        f"/api/v1/calendar?start={WEEK_START.isoformat()}&end=2026-11-08", headers=auth()
    )

    assert response.status_code == 200
    data = response.json()["data"]
    types = [item["type"] for item in data["items"]]
    assert types.count("class") == 1  # one Monday occurrence
    assert types.count("exam") == 1
    assert types.count("assignment_deadline") == 1
    assert all(set(item) >= {"type", "date", "course_id"} for item in data["items"])


@pytest.mark.asyncio
async def test_recurring_class_expands_to_all_matching_weekdays(
    client_and_repo: tuple[AsyncClient, FakeCalendarRepository],
) -> None:
    """Weekly slots expand per occurrence inside the range."""
    client, repo = client_and_repo
    seeded = seeded_repo()
    repo.schedules, repo.exams, repo.deadlines = seeded.schedules, [], []

    response = await client.get(
        "/api/v1/calendar?start=2026-11-02&end=2026-11-15", headers=auth()
    )

    classes = [i for i in response.json()["data"]["items"] if i["type"] == "class"]
    assert [c["date"] for c in classes] == ["2026-11-02", "2026-11-09"]


@pytest.mark.asyncio
async def test_calendar_sorted_chronologically(
    client_and_repo: tuple[AsyncClient, FakeCalendarRepository],
) -> None:
    client, repo = client_and_repo
    seeded = seeded_repo()
    repo.schedules, repo.exams, repo.deadlines = seeded.schedules, seeded.exams, seeded.deadlines

    response = await client.get(
        "/api/v1/calendar?start=2026-11-02&end=2026-11-08", headers=auth()
    )

    items = response.json()["data"]["items"]
    keys = [(i["date"], i["start_time"]) for i in items]
    assert keys == sorted(keys)


@pytest.mark.asyncio
async def test_calendar_is_scoped_to_token_subject(
    client_and_repo: tuple[AsyncClient, FakeCalendarRepository],
) -> None:
    """Another owner's data never appears (isolation)."""
    client, repo = client_and_repo
    seeded = seeded_repo()
    repo.schedules, repo.exams, repo.deadlines = seeded.schedules, seeded.exams, seeded.deadlines

    response = await client.get(
        "/api/v1/calendar?start=2026-11-02&end=2026-11-08", headers=auth(OTHER_ID)
    )

    assert response.status_code == 200
    assert response.json()["data"]["items"] == []


@pytest.mark.asyncio
async def test_invalid_ranges_rejected(
    client_and_repo: tuple[AsyncClient, FakeCalendarRepository],
) -> None:
    client, _ = client_and_repo
    reversed_range = await client.get(
        "/api/v1/calendar?start=2026-11-08&end=2026-11-02", headers=auth()
    )
    too_long = await client.get(
        "/api/v1/calendar?start=2026-01-01&end=2026-12-31", headers=auth()
    )

    assert reversed_range.status_code == 422
    assert too_long.status_code == 422


@pytest.mark.asyncio
async def test_calendar_defaults_to_current_week_when_range_missing(
    client_and_repo: tuple[AsyncClient, FakeCalendarRepository],
) -> None:
    client, _ = client_and_repo
    response = await client.get("/api/v1/calendar", headers=auth())

    assert response.status_code == 200
    assert response.json()["data"]["range"]["start"] <= response.json()["data"]["range"]["end"]


@pytest.mark.asyncio
async def test_calendar_requires_authentication(
    client_and_repo: tuple[AsyncClient, FakeCalendarRepository],
) -> None:
    client, _ = client_and_repo
    response = await client.get("/api/v1/calendar")
    assert response.status_code == 401
