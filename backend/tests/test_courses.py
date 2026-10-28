"""Unit tests for TASK-M2-01: courses CRUD (US: Manage courses)."""

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.courses import get_course_service
from app.core.security import create_access_token
from app.main import app
from app.repositories.course_repo import CourseRepository
from app.services.course_service import CourseService

USER_ID = "77777777-7777-7777-7777-777777777777"
OTHER_ID = "88888888-8888-8888-8888-888888888888"


class FakeCourseRepository(CourseRepository):
    """In-memory courses store keyed by (owner, id)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self.courses: dict[str, dict[str, Any]] = {}
        self.deleted: list[str] = []
        self.counter = 0

    def _add(self, user_id: str, **overrides: Any) -> dict[str, Any]:
        self.counter += 1
        course_id = str(self.counter).rjust(8, "0") + "-0000-0000-0000-000000000000"
        row = {
            "id": course_id,
            "user_id": user_id,
            "course_code": None,
            "name": "Course",
            "description": None,
            "instructor": None,
            "semester": None,
            "academic_year": None,
            "status": "active",
            "created_at": "2026-10-28T00:00:00+00:00",
            "updated_at": "2026-10-28T00:00:00+00:00",
        }
        row.update(overrides)
        self.courses[course_id] = row
        return dict(row)

    async def create_course(self, **kwargs: Any) -> dict[str, Any]:
        return self._add(kwargs["user_id"], **{k: v for k, v in kwargs.items() if k != "user_id"})

    async def list_courses(self, user_id: str, status: str | None = None) -> list[dict[str, Any]]:
        rows = [c for c in self.courses.values() if c["user_id"] == user_id]
        if status is not None:
            rows = [c for c in rows if c["status"] == status]
        return [dict(c) for c in rows]

    async def get_course(self, user_id: str, course_id: str) -> dict[str, Any] | None:
        course = self.courses.get(course_id)
        if course and course["user_id"] == user_id:
            return dict(course)
        return None

    async def get_course_by_natural_key(
        self, *, user_id: str, course_code: str | None, semester: str | None, academic_year: str | None
    ) -> dict[str, Any] | None:
        if course_code is None or semester is None or academic_year is None:
            return None
        for course in self.courses.values():
            if (
                course["user_id"] == user_id
                and course["course_code"] == course_code
                and course["semester"] == semester
                and course["academic_year"] == academic_year
            ):
                return dict(course)
        return None

    async def update_course(
        self, user_id: str, course_id: str, changes: dict[str, Any]
    ) -> dict[str, Any] | None:
        course = self.courses.get(course_id)
        if not course or course["user_id"] != user_id:
            return None
        course.update(changes)
        return dict(course)

    async def delete_course_with_counts(self, user_id: str, course_id: str) -> dict[str, Any] | None:
        course = await self.get_course(user_id, course_id)
        if course is None:
            return None
        del self.courses[course_id]
        self.deleted.append(course_id)
        return {"assignments": 3, "exams": 2, "class_schedules": 1, "documents": 4}


@pytest_asyncio.fixture
async def client_and_repo() -> AsyncGenerator[tuple[AsyncClient, FakeCourseRepository], None]:
    repo = FakeCourseRepository()

    async def override_course_service() -> CourseService:
        return CourseService(course_repo=repo)

    app.dependency_overrides[get_course_service] = override_course_service
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, repo
    app.dependency_overrides.clear()


VALID_COURSE = {
    "name": "Machine Learning",
    "description": "Intro to ML",
    "course_code": "CS402",
    "instructor": "Dr. Phong",
    "semester": "HK1",
    "academic_year": "2026-2027",
}


def auth(user_id: str = USER_ID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


@pytest.mark.asyncio
async def test_create_course_returns_201_owned_by_token_subject(
    client_and_repo: tuple[AsyncClient, FakeCourseRepository],
) -> None:
    client, repo = client_and_repo
    response = await client.post("/api/v1/courses", json=VALID_COURSE, headers=auth())

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["name"] == "Machine Learning"
    assert data["status"] == "active"
    assert repo.courses[data["id"]]["user_id"] == USER_ID


@pytest.mark.asyncio
async def test_create_duplicate_natural_key_rejected_409(
    client_and_repo: tuple[AsyncClient, FakeCourseRepository],
) -> None:
    client, repo = client_and_repo
    first = await client.post("/api/v1/courses", json=VALID_COURSE, headers=auth())
    assert first.status_code == 201

    second = await client.post("/api/v1/courses", json=VALID_COURSE, headers=auth())

    assert second.status_code == 409
    assert "already exists" in second.json()["error"]["message"]


@pytest.mark.asyncio
async def test_create_allows_same_key_for_different_users(
    client_and_repo: tuple[AsyncClient, FakeCourseRepository],
) -> None:
    """Uniqueness is per owner (user_id, code, semester, year)."""
    client, _ = client_and_repo
    first = await client.post("/api/v1/courses", json=VALID_COURSE, headers=auth(USER_ID))
    second = await client.post("/api/v1/courses", json=VALID_COURSE, headers=auth(OTHER_ID))

    assert first.status_code == second.status_code == 201


@pytest.mark.asyncio
async def test_create_rejects_blank_name_and_bad_status(
    client_and_repo: tuple[AsyncClient, FakeCourseRepository],
) -> None:
    client, _ = client_and_repo
    blank = await client.post("/api/v1/courses", json={"name": "  "}, headers=auth())
    assert blank.status_code == 422


@pytest.mark.asyncio
async def test_list_courses_filters_by_status(
    client_and_repo: tuple[AsyncClient, FakeCourseRepository],
) -> None:
    client, repo = client_and_repo
    created = await client.post("/api/v1/courses", json=VALID_COURSE, headers=auth())
    course_id = created.json()["data"]["id"]
    await client.patch(f"/api/v1/courses/{course_id}", json={"status": "archived"}, headers=auth())
    await client.post("/api/v1/courses", json={"name": "Second"}, headers=auth())

    active = await client.get("/api/v1/courses", headers=auth())
    archived = await client.get("/api/v1/courses?status=archived", headers=auth())

    assert active.status_code == 200
    assert [c["name"] for c in active.json()["data"]] == ["Second"]
    assert [c["id"] for c in archived.json()["data"]] == [course_id]


@pytest.mark.asyncio
async def test_get_other_users_course_returns_404(
    client_and_repo: tuple[AsyncClient, FakeCourseRepository],
) -> None:
    """Isolation: another owner's course is invisible (404, not 403)."""
    client, repo = client_and_repo
    created = await client.post("/api/v1/courses", json=VALID_COURSE, headers=auth(USER_ID))
    course_id = created.json()["data"]["id"]

    response = await client.get(f"/api/v1/courses/{course_id}", headers=auth(OTHER_ID))

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_course_applies_provided_fields(
    client_and_repo: tuple[AsyncClient, FakeCourseRepository],
) -> None:
    client, repo = client_and_repo
    created = await client.post("/api/v1/courses", json=VALID_COURSE, headers=auth())
    course_id = created.json()["data"]["id"]

    response = await client.patch(
        f"/api/v1/courses/{course_id}",
        json={"instructor": "Dr. New", "status": "archived"},
        headers=auth(),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["instructor"] == "Dr. New"
    assert data["status"] == "archived"
    assert data["name"] == "Machine Learning"  # untouched


@pytest.mark.asyncio
async def test_update_conflicting_natural_key_rejected_409(
    client_and_repo: tuple[AsyncClient, FakeCourseRepository],
) -> None:
    client, _ = client_and_repo
    await client.post("/api/v1/courses", json=VALID_COURSE, headers=auth())
    second = await client.post("/api/v1/courses", json={"name": "Other"}, headers=auth())
    other_id = second.json()["data"]["id"]

    conflict = await client.patch(
        f"/api/v1/courses/{other_id}", json={"course_code": "CS402", "semester": "HK1", "academic_year": "2026-2027"}, headers=auth()
    )

    assert conflict.status_code == 409


@pytest.mark.asyncio
async def test_delete_reports_linked_counts(
    client_and_repo: tuple[AsyncClient, FakeCourseRepository],
) -> None:
    """US: delete must state the effect on linked assignments/exams/documents."""
    client, repo = client_and_repo
    created = await client.post("/api/v1/courses", json=VALID_COURSE, headers=auth())
    course_id = created.json()["data"]["id"]

    response = await client.delete(f"/api/v1/courses/{course_id}", headers=auth())

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["deleted"] is True
    assert data["linked_counts"] == {
        "assignments": 3,
        "exams": 2,
        "class_schedules": 1,
        "documents": 4,
    }
    assert repo.deleted == [course_id]


@pytest.mark.asyncio
async def test_delete_missing_course_returns_404(
    client_and_repo: tuple[AsyncClient, FakeCourseRepository],
) -> None:
    client, _ = client_and_repo
    response = await client.delete(
        "/api/v1/courses/00000000-0000-0000-0000-000000000000", headers=auth()
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_courses_require_authentication(
    client_and_repo: tuple[AsyncClient, FakeCourseRepository],
) -> None:
    client, _ = client_and_repo
    response = await client.get("/api/v1/courses")
    assert response.status_code == 401
