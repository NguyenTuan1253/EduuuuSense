"""Unit tests for TASK-M2-02: assignments management (US 2.7)."""

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.assignments import get_assignment_service
from app.core.security import create_access_token
from app.main import app
from app.repositories.assignment_repo import AssignmentRepository
from app.services.assignment_service import AssignmentService

USER_ID = "99999999-9999-9999-9999-999999999999"
OTHER_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
COURSE_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
OTHER_COURSE_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"
ASSIGNMENT_ID = "dddddddd-dddd-dddd-dddd-dddddddddddd"


class FakeAssignmentRepository(AssignmentRepository):
    """In-memory assignments store; courses are canned per owner."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self.course_owners: dict[str, str] = {COURSE_ID: USER_ID, OTHER_COURSE_ID: OTHER_ID}
        self.assignments: dict[str, dict[str, Any]] = {}
        self.deleted: list[str] = []

    def _base_row(self, **overrides: Any) -> dict[str, Any]:
        row: dict[str, Any] = {
            "id": ASSIGNMENT_ID,
            "course_id": COURSE_ID,
            "title": "Assignment",
            "description": None,
            "due_at": None,
            "priority": "medium",
            "status": "pending",
            "completed_at": None,
            "created_at": "2026-10-28T00:00:00+00:00",
            "updated_at": "2026-10-28T00:00:00+00:00",
        }
        row.update(overrides)
        return row

    async def create_assignment(self, **kwargs: Any) -> dict[str, Any] | None:
        if self.course_owners.get(kwargs["course_id"]) != kwargs["user_id"]:
            return None
        self.assignments[kwargs.get("title", "new") + str(len(self.assignments))] = (
            self._base_row(course_id=kwargs["course_id"], **{
                k: v for k, v in kwargs.items() if k in ("title", "description", "due_at", "priority")
            })
        )
        return dict(next(iter(self.assignments.values())))

    async def list_assignments(
        self, user_id: str, course_id: str, status: str | None = None
    ) -> list[dict[str, Any]]:
        if self.course_owners.get(course_id) != user_id:
            return []
        rows = [a for a in self.assignments.values() if a["course_id"] == course_id]
        if status is not None:
            rows = [a for a in rows if a["status"] == status]
        return [dict(a) for a in rows]

    async def get_assignment(self, user_id: str, assignment_id: str) -> dict[str, Any] | None:
        assignment = self.assignments.get(assignment_id)
        if assignment and self.course_owners.get(assignment["course_id"]) == user_id:
            return dict(assignment)
        return None

    async def update_assignment(
        self, user_id: str, assignment_id: str, changes: dict[str, Any]
    ) -> dict[str, Any] | None:
        assignment = self.assignments.get(assignment_id)
        if not assignment or self.course_owners.get(assignment["course_id"]) != user_id:
            return None
        assignment.update(changes)
        return dict(assignment)

    async def delete_assignment(self, user_id: str, assignment_id: str) -> bool:
        assignment = self.assignments.get(assignment_id)
        if not assignment or self.course_owners.get(assignment["course_id"]) != user_id:
            return False
        del self.assignments[assignment_id]
        self.deleted.append(assignment_id)
        return True


@pytest_asyncio.fixture
async def client_and_repo() -> AsyncGenerator[tuple[AsyncClient, FakeAssignmentRepository], None]:
    repo = FakeAssignmentRepository()

    async def override_assignment_service() -> AssignmentService:
        return AssignmentService(assignment_repo=repo)

    app.dependency_overrides[get_assignment_service] = override_assignment_service
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, repo
    app.dependency_overrides.clear()


def auth(user_id: str = USER_ID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


VALID = {"title": "HW1", "description": "Chapter 1", "due_at": "2026-11-15T23:59:00+07:00"}


@pytest.mark.asyncio
async def test_create_assignment_defaults_and_ownership(
    client_and_repo: tuple[AsyncClient, FakeAssignmentRepository],
) -> None:
    client, repo = client_and_repo
    response = await client.post(
        f"/api/v1/courses/{COURSE_ID}/assignments", json=VALID, headers=auth()
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["priority"] == "medium"
    assert data["status"] == "pending"
    assert data["completed_at"] is None
    assert data["course_id"] == COURSE_ID
    assert repo.assignments  # recorded


@pytest.mark.asyncio
async def test_create_under_foreign_or_missing_course_is_404(
    client_and_repo: tuple[AsyncClient, FakeAssignmentRepository],
) -> None:
    client, _ = client_and_repo
    foreign = await client.post(
        f"/api/v1/courses/{OTHER_COURSE_ID}/assignments", json=VALID, headers=auth()
    )
    missing = await client.post(
        "/api/v1/courses/00000000-0000-0000-0000-000000000000/assignments", json=VALID, headers=auth()
    )

    assert foreign.status_code == 404
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_status_transition_to_completed_stamps_completed_at(
    client_and_repo: tuple[AsyncClient, FakeAssignmentRepository],
) -> None:
    client, repo = client_and_repo
    repo.assignments[ASSIGNMENT_ID] = repo._base_row()

    done = await client.patch(
        f"/api/v1/assignments/{ASSIGNMENT_ID}", json={"status": "completed"}, headers=auth()
    )
    assert done.status_code == 200
    assert done.json()["data"]["status"] == "completed"
    assert done.json()["data"]["completed_at"] is not None

    reopened = await client.patch(
        f"/api/v1/assignments/{ASSIGNMENT_ID}", json={"status": "in_progress"}, headers=auth()
    )
    assert reopened.json()["data"]["status"] == "in_progress"
    assert reopened.json()["data"]["completed_at"] is None


@pytest.mark.asyncio
async def test_completed_at_is_rejected_as_explicit_input(
    client_and_repo: tuple[AsyncClient, FakeAssignmentRepository],
) -> None:
    """completed_at is derived; a client must not set it directly."""
    client, repo = client_and_repo
    repo.assignments[ASSIGNMENT_ID] = repo._base_row()

    # model_fields_set cannot include completed_at (not a schema field); verify
    # via the service contract directly.
    service = AssignmentService(assignment_repo=repo)
    with pytest.raises(ValueError, match="derived"):
        await service.update_assignment(
            USER_ID, ASSIGNMENT_ID, {"completed_at": "2026-01-01T00:00:00+00:00"}
        )


@pytest.mark.asyncio
async def test_list_filters_by_status_and_respects_ownership(
    client_and_repo: tuple[AsyncClient, FakeAssignmentRepository],
) -> None:
    client, repo = client_and_repo
    repo.assignments[ASSIGNMENT_ID] = repo._base_row(status="completed")
    repo.assignments["eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"] = repo._base_row(
        id="eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee", title="HW2"
    )

    listed = await client.get(f"/api/v1/courses/{COURSE_ID}/assignments", headers=auth())
    completed_only = await client.get(
        f"/api/v1/courses/{COURSE_ID}/assignments?status=completed", headers=auth()
    )
    foreign = await client.get(
        f"/api/v1/courses/{COURSE_ID}/assignments", headers=auth(OTHER_ID)
    )

    assert len(listed.json()["data"]) == 2
    assert [a["id"] for a in completed_only.json()["data"]] == [ASSIGNMENT_ID]
    assert foreign.json()["data"] == []


@pytest.mark.asyncio
async def test_foreign_assignment_is_404_for_patch_and_delete(
    client_and_repo: tuple[AsyncClient, FakeAssignmentRepository],
) -> None:
    client, repo = client_and_repo
    repo.assignments[ASSIGNMENT_ID] = repo._base_row()

    patched = await client.patch(
        f"/api/v1/assignments/{ASSIGNMENT_ID}", json={"title": "X"}, headers=auth(OTHER_ID)
    )
    deleted = await client.delete(
        f"/api/v1/assignments/{ASSIGNMENT_ID}", headers=auth(OTHER_ID)
    )

    assert patched.status_code == 404
    assert deleted.status_code == 404


@pytest.mark.asyncio
async def test_delete_assignment_removes_and_reports(
    client_and_repo: tuple[AsyncClient, FakeAssignmentRepository],
) -> None:
    client, repo = client_and_repo
    repo.assignments[ASSIGNMENT_ID] = repo._base_row()

    response = await client.delete(f"/api/v1/assignments/{ASSIGNMENT_ID}", headers=auth())

    assert response.status_code == 200
    assert response.json()["data"]["deleted"] is True
    assert repo.deleted == [ASSIGNMENT_ID]


@pytest.mark.asyncio
async def test_invalid_priority_and_status_rejected(
    client_and_repo: tuple[AsyncClient, FakeAssignmentRepository],
) -> None:
    client, repo = client_and_repo
    bad_priority = await client.post(
        f"/api/v1/courses/{COURSE_ID}/assignments", json={**VALID, "priority": "urgent"}, headers=auth()
    )
    repo.assignments[ASSIGNMENT_ID] = repo._base_row()
    bad_status = await client.patch(
        f"/api/v1/assignments/{ASSIGNMENT_ID}", json={"status": "done"}, headers=auth()
    )

    assert bad_priority.status_code == 422
    assert bad_status.status_code == 422


@pytest.mark.asyncio
async def test_assignments_require_authentication(
    client_and_repo: tuple[AsyncClient, FakeAssignmentRepository],
) -> None:
    client, _ = client_and_repo
    response = await client.get(f"/api/v1/courses/{COURSE_ID}/assignments")
    assert response.status_code == 401
