"""Unit tests for TASK-M2-03: exam schedule tracking (US 2.8)."""

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.exams import get_exam_service
from app.core.security import create_access_token
from app.main import app
from app.repositories.exam_repo import ExamRepository
from app.services.exam_service import ExamService

USER_ID = "abababab-abab-abab-abab-abababababab"
OTHER_ID = "cdcdcdcd-cdcd-cdcd-cdcd-cdcdcdcdcdcd"
COURSE_ID = "efefefef-efef-efef-efef-efefefefefef"
OTHER_COURSE_ID = "fafafafa-fafa-fafa-fafa-fafafafafafa"
EXAM_ID = "bebebebe-bebe-bebe-bebe-bebebebebebe"


class FakeExamRepository(ExamRepository):
    def __init__(self, session: AsyncSession | None = None) -> None:
        self.course_owners: dict[str, str] = {COURSE_ID: USER_ID, OTHER_COURSE_ID: OTHER_ID}
        self.exams: dict[str, dict[str, Any]] = {}
        self.deleted: list[str] = []

    def _base_row(self, **overrides: Any) -> dict[str, Any]:
        row: dict[str, Any] = {
            "id": EXAM_ID,
            "course_id": COURSE_ID,
            "title": "Final",
            "exam_at": "2026-12-10T07:30:00+07:00",
            "duration_minutes": 90,
            "location": "Room A2",
            "notes": None,
            "created_at": "2026-10-28T00:00:00+00:00",
            "updated_at": "2026-10-28T00:00:00+00:00",
        }
        row.update(overrides)
        return row

    async def create_exam(self, **kwargs: Any) -> dict[str, Any] | None:
        if self.course_owners.get(kwargs["course_id"]) != kwargs["user_id"]:
            return None
        exam_id = f"new-{len(self.exams) + 1}"
        self.exams[exam_id] = self._base_row(
            id=exam_id,
            **{k: v for k, v in kwargs.items() if k in ("course_id", "title", "exam_at", "duration_minutes", "location", "notes")},
        )
        return dict(self.exams[exam_id])

    async def list_exams(self, user_id: str, course_id: str) -> list[dict[str, Any]]:
        if self.course_owners.get(course_id) != user_id:
            return []
        rows = sorted(
            (e for e in self.exams.values() if e["course_id"] == course_id),
            key=lambda e: e["exam_at"],
        )
        return [dict(e) for e in rows]

    async def get_exam(self, user_id: str, exam_id: str) -> dict[str, Any] | None:
        exam = self.exams.get(exam_id)
        if exam and self.course_owners.get(exam["course_id"]) == user_id:
            return dict(exam)
        return None

    async def update_exam(self, user_id: str, exam_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
        exam = self.exams.get(exam_id)
        if not exam or self.course_owners.get(exam["course_id"]) != user_id:
            return None
        exam.update(changes)
        return dict(exam)

    async def delete_exam(self, user_id: str, exam_id: str) -> bool:
        exam = self.exams.get(exam_id)
        if not exam or self.course_owners.get(exam["course_id"]) != user_id:
            return False
        del self.exams[exam_id]
        self.deleted.append(exam_id)
        return True


@pytest_asyncio.fixture
async def client_and_repo() -> AsyncGenerator[tuple[AsyncClient, FakeExamRepository], None]:
    repo = FakeExamRepository()

    async def override_exam_service() -> ExamService:
        return ExamService(exam_repo=repo)

    app.dependency_overrides[get_exam_service] = override_exam_service
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, repo
    app.dependency_overrides.clear()


def auth(user_id: str = USER_ID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


VALID = {
    "title": "Final Exam",
    "exam_at": "2026-12-10T07:30:00+07:00",
    "duration_minutes": 90,
    "location": "Room A2",
}


@pytest.mark.asyncio
async def test_create_exam_returns_201_with_location_and_duration(
    client_and_repo: tuple[AsyncClient, FakeExamRepository],
) -> None:
    client, repo = client_and_repo
    response = await client.post(f"/api/v1/courses/{COURSE_ID}/exams", json=VALID, headers=auth())

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["title"] == "Final Exam"
    assert data["location"] == "Room A2"
    assert data["duration_minutes"] == 90
    assert list(repo.exams.values())[0]["course_id"] == COURSE_ID


@pytest.mark.asyncio
async def test_create_under_foreign_or_missing_course_404(
    client_and_repo: tuple[AsyncClient, FakeExamRepository],
) -> None:
    client, _ = client_and_repo
    foreign = await client.post(
        f"/api/v1/courses/{OTHER_COURSE_ID}/exams", json=VALID, headers=auth()
    )
    missing = await client.post(
        "/api/v1/courses/00000000-0000-0000-0000-000000000000/exams", json=VALID, headers=auth()
    )
    assert foreign.status_code == 404
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_list_exams_sorted_soonest_first_and_scoped(
    client_and_repo: tuple[AsyncClient, FakeExamRepository],
) -> None:
    client, repo = client_and_repo
    repo.exams[EXAM_ID] = repo._base_row(exam_at="2026-12-20T07:30:00+07:00")
    repo.exams["c0c0c0c0-c0c0-c0c0-c0c0-c0c0c0c0c0c0"] = repo._base_row(
        id="c0c0c0c0-c0c0-c0c0-c0c0-c0c0c0c0c0c0", title="Midterm", exam_at="2026-11-20T07:30:00+07:00"
    )

    listed = await client.get(f"/api/v1/courses/{COURSE_ID}/exams", headers=auth())
    foreign = await client.get(f"/api/v1/courses/{COURSE_ID}/exams", headers=auth(OTHER_ID))

    assert [e["title"] for e in listed.json()["data"]] == ["Midterm", "Final"]
    assert foreign.json()["data"] == []


@pytest.mark.asyncio
async def test_update_exam_edits_date_time_location(
    client_and_repo: tuple[AsyncClient, FakeExamRepository],
) -> None:
    client, repo = client_and_repo
    repo.exams[EXAM_ID] = repo._base_row()

    response = await client.patch(
        f"/api/v1/exams/{EXAM_ID}",
        json={"exam_at": "2026-12-12T09:00:00+07:00", "location": "Hall B"},
        headers=auth(),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["exam_at"] == "2026-12-12T09:00:00+07:00"
    assert data["location"] == "Hall B"


@pytest.mark.asyncio
async def test_delete_exam_and_foreign_isolation(
    client_and_repo: tuple[AsyncClient, FakeExamRepository],
) -> None:
    client, repo = client_and_repo
    repo.exams[EXAM_ID] = repo._base_row()

    foreign_delete = await client.delete(f"/api/v1/exams/{EXAM_ID}", headers=auth(OTHER_ID))
    assert foreign_delete.status_code == 404

    own_delete = await client.delete(f"/api/v1/exams/{EXAM_ID}", headers=auth())
    assert own_delete.status_code == 200
    assert repo.deleted == [EXAM_ID]


@pytest.mark.asyncio
async def test_invalid_duration_and_blank_title_rejected(
    client_and_repo: tuple[AsyncClient, FakeExamRepository],
) -> None:
    client, _ = client_and_repo
    bad_duration = await client.post(
        f"/api/v1/courses/{COURSE_ID}/exams", json={**VALID, "duration_minutes": 0}, headers=auth()
    )
    blank = await client.post(
        f"/api/v1/courses/{COURSE_ID}/exams", json={**VALID, "title": " "}, headers=auth()
    )
    missing_time = await client.post(
        f"/api/v1/courses/{COURSE_ID}/exams", json={"title": "Quiz"}, headers=auth()
    )

    assert bad_duration.status_code == 422
    assert blank.status_code == 422
    assert missing_time.status_code == 422


@pytest.mark.asyncio
async def test_exams_require_authentication(
    client_and_repo: tuple[AsyncClient, FakeExamRepository],
) -> None:
    client, _ = client_and_repo
    response = await client.get(f"/api/v1/courses/{COURSE_ID}/exams")
    assert response.status_code == 401
