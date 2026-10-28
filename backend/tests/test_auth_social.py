"""Unit tests for TASK-M1-07: Google social login (US 2.1/2.2, DECISION-PO-01)."""

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.auth import get_auth_service
from app.clients.google_oauth import GoogleOAuthError
from app.core.security import decode_token
from app.main import app
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService

USER_ID = "66666666-6666-6666-6666-666666666666"


class FakeGoogleClient:
    """Injectable Google identity verifier."""

    def __init__(self, claims: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.claims = claims
        self.error = error
        self.calls: list[str] = []

    async def verify_id_token(self, id_token: str) -> dict[str, Any]:
        self.calls.append(id_token)
        if self.error is not None:
            raise self.error
        if self.claims is None:
            raise GoogleOAuthError("No claims configured for the fake client.")
        return dict(self.claims)


class FakeUserRepository(UserRepository):
    def __init__(self, session: AsyncSession | None = None, *, user: dict[str, Any] | None = None) -> None:
        self.user = user
        self.created_social: list[dict[str, Any]] = []
        self.sessions: list[dict[str, Any]] = []

    async def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        if self.user and self.user["email"] == email:
            return dict(self.user)
        return None

    async def create_social_user(self, **kwargs: Any) -> dict[str, Any]:
        self.created_social.append(kwargs)
        return {
            "id": USER_ID,
            "email": kwargs["email"],
            "is_email_verified": True,
            "is_active": True,
        }

    async def create_refresh_session(self, **kwargs: Any) -> str:
        self.sessions.append(kwargs)
        return "session-1"

    async def update_last_login(self, user_id: str) -> None:
        return None


VALID_CLAIMS = {
    "email": "student@example.com",
    "email_verified": "true",
    "name": "Nguyen Van A",
    "sub": "google-sub-123",
}


@pytest_asyncio.fixture
async def client_and_fakes() -> AsyncGenerator[tuple[AsyncClient, FakeUserRepository, FakeGoogleClient], None]:
    repo = FakeUserRepository(
        user={
            "id": USER_ID,
            "email": "student@example.com",
            "password_hash": "argon2hash",
            "is_active": True,
            "is_email_verified": True,
        }
    )
    google = FakeGoogleClient(claims=VALID_CLAIMS)

    async def override_get_auth_service() -> AuthService:
        return AuthService(user_repo=repo, google_client=google)

    app.dependency_overrides[get_auth_service] = override_get_auth_service
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, repo, google
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_social_login_existing_user_issues_tokens(
    client_and_fakes: tuple[AsyncClient, FakeUserRepository, FakeGoogleClient],
) -> None:
    """Verified Google email matching an existing account logs the user in."""
    client, repo, _ = client_and_fakes
    response = await client.post(
        "/api/v1/auth/social/google", json={"id_token": "valid-google-id-token"}
    )

    assert response.status_code == 200
    data = response.json()["data"]
    claims = decode_token(data["access_token"], expected_type="access")
    assert claims["sub"] == USER_ID
    assert len(repo.sessions) == 1
    assert repo.created_social == []


@pytest.mark.asyncio
async def test_social_login_unknown_email_auto_registers_verified_account(
    client_and_fakes: tuple[AsyncClient, FakeUserRepository, FakeGoogleClient],
) -> None:
    """Unknown Google email creates an account with an unusable password."""
    client, repo, google = client_and_fakes
    repo.user = None
    response = await client.post(
        "/api/v1/auth/social/google", json={"id_token": "valid-google-id-token"}
    )

    assert response.status_code == 200
    assert len(repo.created_social) == 1
    created = repo.created_social[0]
    assert created["email"] == "student@example.com"
    assert created["full_name"] == "Nguyen Van A"
    # The unusable hash must never match the (absent) plaintext password.
    assert created["password_hash"].startswith("$argon2")


@pytest.mark.asyncio
async def test_social_login_rejects_unverified_google_email(
    client_and_fakes: tuple[AsyncClient, FakeUserRepository, FakeGoogleClient],
) -> None:
    """Google email not verified -> 401, no user created, no session."""
    client, repo, google = client_and_fakes
    google.claims = {**VALID_CLAIMS, "email_verified": "false"}
    response = await client.post(
        "/api/v1/auth/social/google", json={"id_token": "valid-google-id-token"}
    )

    assert response.status_code == 401
    assert repo.created_social == []
    assert repo.sessions == []


@pytest.mark.asyncio
async def test_social_login_rejects_missing_email_claim(
    client_and_fakes: tuple[AsyncClient, FakeUserRepository, FakeGoogleClient],
) -> None:
    client, repo, google = client_and_fakes
    google.claims = {"sub": "x", "email_verified": "true"}
    response = await client.post(
        "/api/v1/auth/social/google", json={"id_token": "valid-google-id-token"}
    )

    assert response.status_code == 401
    assert repo.sessions == []


@pytest.mark.asyncio
async def test_social_login_maps_client_errors_to_generic_401(
    client_and_fakes: tuple[AsyncClient, FakeUserRepository, FakeGoogleClient],
) -> None:
    """Verification failure (bad token, audience mismatch, network) -> 401."""
    client, _, google = client_and_fakes
    google.error = GoogleOAuthError("ID token audience mismatch.")
    response = await client.post(
        "/api/v1/auth/social/google", json={"id_token": "tampered-google-token"}
    )

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Google sign-in failed."


@pytest.mark.asyncio
async def test_social_login_suspended_account_gets_403(
    client_and_fakes: tuple[AsyncClient, FakeUserRepository, FakeGoogleClient],
) -> None:
    client, repo, _ = client_and_fakes
    assert repo.user is not None
    repo.user = {**repo.user, "is_active": False}
    response = await client.post(
        "/api/v1/auth/social/google", json={"id_token": "valid-google-id-token"}
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_social_login_full_name_capped_at_150_chars(
    client_and_fakes: tuple[AsyncClient, FakeUserRepository, FakeGoogleClient],
) -> None:
    """Provider-supplied names are truncated to the profile column size."""
    client, repo, google = client_and_fakes
    repo.user = None
    google.claims = {**VALID_CLAIMS, "name": "N" * 400}
    response = await client.post(
        "/api/v1/auth/social/google", json={"id_token": "valid-google-id-token"}
    )

    assert response.status_code == 200
    assert len(repo.created_social[0]["full_name"]) == 150


@pytest.mark.asyncio
async def test_social_login_never_exposes_password_material(
    client_and_fakes: tuple[AsyncClient, FakeUserRepository, FakeGoogleClient],
) -> None:
    client, _, _ = client_and_fakes
    response = await client.post(
        "/api/v1/auth/social/google", json={"id_token": "valid-google-id-token"}
    )

    assert response.status_code == 200
    assert "password" not in response.text.lower()


@pytest.mark.asyncio
async def test_access_token_from_social_login_is_valid_for_profile_apis(
    client_and_fakes: tuple[AsyncClient, FakeUserRepository, FakeGoogleClient],
) -> None:
    """The issued access token carries the expected subject/type claims."""
    client, _, _ = client_and_fakes
    response = await client.post(
        "/api/v1/auth/social/google", json={"id_token": "valid-google-id-token"}
    )
    access = response.json()["data"]["access_token"]
    claims = decode_token(access, expected_type="access")
    assert claims["type"] == "access"
    assert claims["sub"] == USER_ID
