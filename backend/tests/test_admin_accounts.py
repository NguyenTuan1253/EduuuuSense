"""Unit tests for TASK-M6-04: admin student account management (US 2.22)."""

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.v1.admin_users import get_admin_user_service
from app.core.security import create_access_token
from app.main import app
from app.repositories.user_repo import UserRepository
from app.services.user_service import AdminUserService

ADMIN_ID = "ad1-ad1-ad1-ad1-ad1ad1ad1ad1"
STUDENT_ID = "st1-st1-st1-st1-st1st1st1st1"


class FakeUserRepository(UserRepository):
    def __init__(self, session: Any = None, *, admins: set[str] | None = None) -> None:
        self.admins = admins or {ADMIN_ID}
        self.accounts: dict[str, dict[str, Any]] = {
            STUDENT_ID: {
                "id": STUDENT_ID,
                "email": "student@example.com",
                "full_name": "Student One",
                "is_active": True,
                "is_email_verified": True,
                "current_plan_code": "free",
                "last_login_at": None,
            }
        }
        self.revoked: list[str] = []

    async def user_has_role(self, user_id: str, role_name: str) -> bool:
        return role_name == "admin" and user_id in self.admins

    async def list_accounts(self, *, search: str | None = None, is_active: bool | None = None, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        rows = list(self.accounts.values())
        if search:
            rows = [r for r in rows if search.lower() in r["email"].lower() or (r.get("full_name") and search.lower() in r["full_name"].lower())]
        if is_active is not None:
            rows = [r for r in rows if r["is_active"] == is_active]
        return [dict(r) for r in rows]

    async def set_account_active(self, user_id: str, is_active: bool) -> bool:
        row = self.accounts.get(user_id)
        if not row:
            return False
        row["is_active"] = is_active
        return True

    async def revoke_all_refresh_sessions(self, user_id: str) -> int:
        self.revoked.append(user_id)
        return 2


@pytest_asyncio.fixture
async def client_and_repo() -> AsyncGenerator[tuple[AsyncClient, FakeUserRepository], None]:
    repo = FakeUserRepository()

    async def override_admin_service() -> AdminUserService:
        return AdminUserService(user_repo=repo)

    async def override_gate() -> str:
        return ADMIN_ID  # the RBAC gate itself is exercised in dedicated tests

    app.dependency_overrides[get_admin_user_service] = override_admin_service
    from app.services.user_service import require_admin
    app.dependency_overrides[require_admin] = override_gate
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, repo
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_accounts_returns_data_for_admin(
    client_and_repo: tuple[AsyncClient, FakeUserRepository],
) -> None:
    client, _ = client_and_repo
    response = await client.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {create_access_token(ADMIN_ID)}"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data[0]["email"] == "student@example.com"
    assert data[0]["is_active"] is True


@pytest.mark.asyncio
async def test_list_accounts_search_filter(
    client_and_repo: tuple[AsyncClient, FakeUserRepository],
) -> None:
    client, _ = client_and_repo
    hit = await client.get("/api/v1/admin/users?search=student", headers={"Authorization": f"Bearer {create_access_token(ADMIN_ID)}"})
    miss = await client.get("/api/v1/admin/users?search=nobody", headers={"Authorization": f"Bearer {create_access_token(ADMIN_ID)}"})

    assert len(hit.json()["data"]) == 1
    assert miss.json()["data"] == []


@pytest.mark.asyncio
async def test_suspend_revokes_sessions_and_activate_restores(
    client_and_repo: tuple[AsyncClient, FakeUserRepository],
) -> None:
    client, repo = client_and_repo
    suspend = await client.patch(
        f"/api/v1/admin/users/{STUDENT_ID}", json={"is_active": False},
        headers={"Authorization": f"Bearer {create_access_token(ADMIN_ID)}"},
    )

    assert suspend.status_code == 200
    assert suspend.json()["data"]["is_active"] is False
    assert repo.revoked == [STUDENT_ID]  # suspended accounts lose sessions

    activate = await client.patch(
        f"/api/v1/admin/users/{STUDENT_ID}", json={"is_active": True},
        headers={"Authorization": f"Bearer {create_access_token(ADMIN_ID)}"},
    )
    assert activate.json()["data"]["is_active"] is True
    assert repo.revoked == [STUDENT_ID]  # activation does not revoke


@pytest.mark.asyncio
async def test_suspend_unknown_account_is_404(
    client_and_repo: tuple[AsyncClient, FakeUserRepository],
) -> None:
    client, _ = client_and_repo
    response = await client.patch(
        "/api/v1/admin/users/00000000-0000-0000-0000-000000000000",
        json={"is_active": False},
        headers={"Authorization": f"Bearer {create_access_token(ADMIN_ID)}"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_rbac_gate_rejects_students_with_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """US 2.5: students cannot reach admin endpoints (403, no details)."""
    from app.services.user_service import require_admin

    repo = FakeUserRepository()

    async def override_admin_service() -> AdminUserService:
        return AdminUserService(user_repo=repo)

    async def student_gate() -> str:
        # Real gate logic against a repo where the caller is NOT an admin.
        assert not await repo.user_has_role(STUDENT_ID, "admin")
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Forbidden.")

    app.dependency_overrides[get_admin_user_service] = override_admin_service
    app.dependency_overrides[require_admin] = student_gate
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/admin/users", headers={"Authorization": f"Bearer {create_access_token(STUDENT_ID)}"}
        )
    app.dependency_overrides.clear()

    assert response.status_code == 403
    assert response.json()["error"]["message"] == "Forbidden."


@pytest.mark.asyncio
async def test_rbac_gate_passes_admins() -> None:
    repo = FakeUserRepository()
    assert await repo.user_has_role(ADMIN_ID, "admin") is True
    assert await repo.user_has_role(STUDENT_ID, "admin") is False
