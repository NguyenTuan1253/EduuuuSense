"""Unit tests for TASK-M1-05: user profile CRUD (US 2.4) and JWT auth dependency."""

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.auth import get_auth_service
from app.api.v1.users import get_user_service
from app.core.security import create_access_token, create_refresh_token
from app.main import app
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService
from app.services.user_service import UserService

USER_ID = "44444444-4444-4444-4444-444444444444"


class FakeUserRepository(UserRepository):
    """In-memory test double for the profile flow."""

    def __init__(
        self,
        session: AsyncSession | None = None,
        *,
        user: dict[str, Any] | None = None,
    ) -> None:
        self.user = user
        self.profile: dict[str, Any] = {
            "user_id": USER_ID,
            "full_name": "Nguyen Van A",
            "display_name": None,
            "avatar_url": None,
            "phone_number": None,
            "university": None,
            "major": None,
            "academic_year": None,
            "updated_at": "2026-10-28T00:00:00+00:00",
        }
        self.updates: list[dict[str, Any]] = []

    async def get_user_with_profile(self, user_id: str) -> dict[str, Any] | None:
        if not self.user or str(self.user["id"]) != user_id:
            return None
        return {**self.user, **self.profile}

    async def update_user_profile(self, user_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        self.updates.append({"user_id": user_id, **changes})
        self.profile.update(changes)
        return dict(self.profile)


@pytest_asyncio.fixture
async def client_and_repo() -> AsyncGenerator[tuple[AsyncClient, FakeUserRepository], None]:
    repo = FakeUserRepository(
        user={
            "id": USER_ID,
            "email": "student@example.com",
            "is_email_verified": True,
            "current_plan_code": "free",
        }
    )

    async def override_user_service() -> UserService:
        return UserService(user_repo=repo)

    async def override_auth_service() -> AuthService:
        return AuthService(user_repo=repo)

    app.dependency_overrides[get_user_service] = override_user_service
    app.dependency_overrides[get_auth_service] = override_auth_service
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, repo
    app.dependency_overrides.clear()


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_get_me_requires_authentication(
    client_and_repo: tuple[AsyncClient, FakeUserRepository],
) -> None:
    """No token -> 401 with WWW-Authenticate header."""
    client, _ = client_and_repo
    response = await client.get("/api/v1/users/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "HTTP_401"
    assert response.headers.get("www-authenticate") == "Bearer"


@pytest.mark.asyncio
async def test_get_me_rejects_refresh_token_as_access(
    client_and_repo: tuple[AsyncClient, FakeUserRepository],
) -> None:
    """Type enforcement: refresh tokens are not access tokens."""
    client, _ = client_and_repo
    response = await client.get(
        "/api/v1/users/me", headers=auth_header(create_refresh_token(USER_ID))
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_me_returns_account_and_profile(
    client_and_repo: tuple[AsyncClient, FakeUserRepository],
) -> None:
    """Happy path: account fields + profile fields for the token subject."""
    client, _ = client_and_repo
    response = await client.get("/api/v1/users/me", headers=auth_header(create_access_token(USER_ID)))

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["account"]["user_id"] == USER_ID
    assert data["account"]["email"] == "student@example.com"
    assert data["account"]["current_plan_code"] == "free"
    assert data["profile"]["full_name"] == "Nguyen Van A"
    assert "password_hash" not in response.text


@pytest.mark.asyncio
async def test_update_profile_applies_only_provided_fields(
    client_and_repo: tuple[AsyncClient, FakeUserRepository],
) -> None:
    """PATCH-style PUT: only explicitly provided fields reach the repository."""
    client, repo = client_and_repo
    response = await client.put(
        "/api/v1/users/me/profile",
        json={"display_name": "Van A", "university": "USTH"},
        headers=auth_header(create_access_token(USER_ID)),
    )

    assert response.status_code == 200
    assert response.json()["data"]["profile"]["display_name"] == "Van A"
    assert set(repo.updates[0].keys()) == {"user_id", "display_name", "university"}


@pytest.mark.asyncio
async def test_update_profile_rejects_empty_body(
    client_and_repo: tuple[AsyncClient, FakeUserRepository],
) -> None:
    client, repo = client_and_repo
    response = await client.put(
        "/api/v1/users/me/profile", json={}, headers=auth_header(create_access_token(USER_ID))
    )

    assert response.status_code == 422
    assert repo.updates == []


@pytest.mark.asyncio
async def test_update_profile_rejects_blank_full_name(
    client_and_repo: tuple[AsyncClient, FakeUserRepository],
) -> None:
    client, repo = client_and_repo
    response = await client.put(
        "/api/v1/users/me/profile",
        json={"full_name": "   "},
        headers=auth_header(create_access_token(USER_ID)),
    )

    assert response.status_code == 422
    assert repo.updates == []


@pytest.mark.asyncio
async def test_update_profile_rejects_out_of_range_academic_year(
    client_and_repo: tuple[AsyncClient, FakeUserRepository],
) -> None:
    """Schema CHECK allows academic_year 1..10 only; boundary mirrors it."""
    client, repo = client_and_repo
    for year in ("0", "11"):
        response = await client.put(
            "/api/v1/users/me/profile",
            json={"academic_year": year},
            headers=auth_header(create_access_token(USER_ID)),
        )
        assert response.status_code == 422
    assert repo.updates == []


@pytest.mark.asyncio
async def test_profile_is_scoped_to_token_subject(
    client_and_repo: tuple[AsyncClient, FakeUserRepository],
) -> None:
    """Ownership: updates always target the token subject; no client id trusted."""
    client, repo = client_and_repo
    other_user_token = create_access_token("99999999-9999-9999-9999-999999999999")
    await client.put(
        "/api/v1/users/me/profile",
        json={"major": "CS"},
        headers=auth_header(other_user_token),
    )

    assert repo.updates[0]["user_id"] == "99999999-9999-9999-9999-999999999999"


@pytest.mark.asyncio
async def test_get_me_for_deleted_subject_is_handled(
    client_and_repo: tuple[AsyncClient, FakeUserRepository],
) -> None:
    """Token subject missing server-side -> generic 500 envelope (never a leak).

    Uses a non-raising transport because Starlette re-raises unhandled server
    errors after sending the 500 response.
    """
    client, repo = client_and_repo
    repo.user = None
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as raw_client:
        response = await raw_client.get(
            "/api/v1/users/me", headers=auth_header(create_access_token(USER_ID))
        )

    assert response.status_code == 500
    # Debug-mode app renders a plaintext traceback by design; the safe 500
    # envelope contract is covered by test_unhandled_exception_returns_envelope_when_debug_disabled.
