"""Unit tests for TASK-M1-04: password reset via single-use token (US 2.3)."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from fastapi import status
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.auth import get_auth_service
from app.core.security import hash_password, hash_verification_token, verify_password
from app.main import app
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService, InvalidResetTokenError

USER_ID = "33333333-3333-3333-3333-333333333333"
USER_EMAIL = "student@example.com"


class FakeUserRepository(UserRepository):
    """In-memory test double for the reset flow."""

    def __init__(
        self,
        session: AsyncSession | None = None,
        *,
        user: dict[str, Any] | None = None,
    ) -> None:
        self.user = user
        self.tokens: dict[str, dict[str, Any]] = {}  # keyed by token_hash
        self.password_updates: list[tuple[str, str]] = []
        self.consumed: list[str] = []
        self.attempt_bumps: list[str] = []
        self.revoked_sessions: list[str] = []

    async def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        if self.user and self.user["email"] == email:
            return dict(self.user)
        return None

    async def create_verification_token(self, **kwargs: Any) -> str:
        self.tokens[kwargs["token_hash"]] = {
            "id": f"token-{len(self.tokens) + 1}",
            "user_id": kwargs["user_id"],
            "purpose": kwargs["purpose"],
            "token_hash": kwargs["token_hash"],
            "expires_at": kwargs["expires_at"],
            "consumed_at": None,
            "attempt_count": 0,
        }
        return self.tokens[kwargs["token_hash"]]["id"]

    async def get_verification_token_by_hash(self, token_hash: str, purpose: str) -> dict[str, Any] | None:
        row = self.tokens.get(token_hash)
        if row and row["purpose"] == purpose:
            return dict(row)
        return None

    async def increment_token_attempts(self, token_id: str) -> None:
        self.attempt_bumps.append(token_id)

    async def consume_verification_token(self, token_id: str) -> None:
        self.consumed.append(token_id)
        for row in self.tokens.values():
            if row["id"] == token_id:
                row["consumed_at"] = datetime.now(tz=UTC)

    async def update_user_password(self, user_id: str, password_hash: str) -> None:
        self.password_updates.append((user_id, password_hash))

    async def revoke_all_refresh_sessions(self, user_id: str) -> int:
        self.revoked_sessions.append(user_id)
        return 2  # canned count


@pytest_asyncio.fixture
async def client_and_repo() -> AsyncGenerator[tuple[AsyncClient, FakeUserRepository], None]:
    repo = FakeUserRepository(
        user={
            "id": USER_ID,
            "email": USER_EMAIL,
            "password_hash": hash_password("OldPass1"),
            "is_active": True,
            "is_email_verified": True,
        }
    )

    async def override_get_auth_service() -> AuthService:
        return AuthService(user_repo=repo)

    app.dependency_overrides[get_auth_service] = override_get_auth_service
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, repo
    app.dependency_overrides.clear()


async def _request_reset(client: AsyncClient, email: str = USER_EMAIL) -> dict[str, Any]:
    response = await client.post("/api/v1/auth/password/reset-request", json={"email": email})
    assert response.status_code == 200
    return response.json()


@pytest.mark.asyncio
async def test_reset_request_existing_email_stages_15min_single_use_token(
    client_and_repo: tuple[AsyncClient, FakeUserRepository],
) -> None:
    """US 2.3: time-limited (~15 min) single-use reset token staged for the user."""
    client, repo = client_and_repo
    body = await _request_reset(client)

    assert body["success"] is True
    assert len(repo.tokens) == 1
    token_row = next(iter(repo.tokens.values()))
    assert token_row["purpose"] == "password_reset"
    assert token_row["user_id"] == USER_ID
    remaining = token_row["expires_at"] - datetime.now(tz=UTC)
    assert timedelta(minutes=14) < remaining <= timedelta(minutes=15)


@pytest.mark.asyncio
async def test_reset_request_unknown_email_is_indistinguishable(
    client_and_repo: tuple[AsyncClient, FakeUserRepository],
) -> None:
    """No user enumeration: unknown email returns the same 200 body, no token."""
    client, repo = client_and_repo
    known = await _request_reset(client)
    unknown_response = await client.post(
        "/api/v1/auth/password/reset-request", json={"email": "nobody@example.com"}
    )

    assert unknown_response.status_code == 200
    assert unknown_response.json() == known
    assert len(repo.tokens) == 1  # only the first (existing) request staged a token


@pytest.mark.asyncio
async def test_reset_confirm_rotates_password_and_revokes_all_sessions(
    client_and_repo: tuple[AsyncClient, FakeUserRepository],
) -> None:
    """US 2.3: single use, password rotated, all sessions invalidated."""
    client, repo = client_and_repo
    await _request_reset(client)
    token_row = next(iter(repo.tokens.values()))
    raw_token = "a" * 32
    # Re-stage the token so its hash matches a known raw value.
    repo.tokens = {
        hash_verification_token(raw_token): {**token_row, "token_hash": hash_verification_token(raw_token)}
    }

    response = await client.post(
        "/api/v1/auth/password/reset-confirm",
        json={"token": raw_token, "new_password": "NewPass123"},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    user_id, new_hash = repo.password_updates[0]
    assert user_id == USER_ID
    assert verify_password("NewPass123", new_hash)
    assert not verify_password("OldPass1", new_hash)
    assert repo.consumed == [token_row["id"]]
    assert repo.revoked_sessions == [USER_ID]


@pytest.mark.asyncio
async def test_reset_confirm_rejects_unknown_token_generically(
    client_and_repo: tuple[AsyncClient, FakeUserRepository],
) -> None:
    """Unknown token -> 400 generic; nothing touched."""
    client, repo = client_and_repo
    response = await client.post(
        "/api/v1/auth/password/reset-confirm",
        json={"token": "b" * 32, "new_password": "NewPass123"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "Invalid or expired reset link."
    assert repo.password_updates == []


@pytest.mark.asyncio
async def test_reset_confirm_rejects_wrong_purpose_token(
    client_and_repo: tuple[AsyncClient, FakeUserRepository],
) -> None:
    """An email-verification token must not work as a password-reset token."""
    client, repo = client_and_repo
    raw_token = "c" * 32
    repo.tokens[hash_verification_token(raw_token)] = {
        "id": "token-ev",
        "user_id": USER_ID,
        "purpose": "email_verification",
        "token_hash": hash_verification_token(raw_token),
        "expires_at": datetime.now(tz=UTC) + timedelta(hours=1),
        "consumed_at": None,
        "attempt_count": 0,
    }

    response = await client.post(
        "/api/v1/auth/password/reset-confirm",
        json={"token": raw_token, "new_password": "NewPass123"},
    )

    assert response.status_code == 400
    assert repo.password_updates == []


@pytest.mark.asyncio
async def test_reset_confirm_expired_token_counts_attempt(
    client_and_repo: tuple[AsyncClient, FakeUserRepository],
) -> None:
    """Expired token -> 400, attempt_count incremented, password untouched."""
    client, repo = client_and_repo
    raw_token = "d" * 32
    repo.tokens[hash_verification_token(raw_token)] = {
        "id": "token-expired",
        "user_id": USER_ID,
        "purpose": "password_reset",
        "token_hash": hash_verification_token(raw_token),
        "expires_at": datetime.now(tz=UTC) - timedelta(minutes=1),
        "consumed_at": None,
        "attempt_count": 0,
    }

    response = await client.post(
        "/api/v1/auth/password/reset-confirm",
        json={"token": raw_token, "new_password": "NewPass123"},
    )

    assert response.status_code == 400
    assert repo.attempt_bumps == ["token-expired"]
    assert repo.password_updates == []


@pytest.mark.asyncio
async def test_reset_confirm_rejects_weak_new_password(
    client_and_repo: tuple[AsyncClient, FakeUserRepository],
) -> None:
    """US 2.3: the new password must meet the policy (422 at the boundary)."""
    client, repo = client_and_repo
    await _request_reset(client)

    response = await client.post(
        "/api/v1/auth/password/reset-confirm",
        json={"token": "e" * 32, "new_password": "allletters"},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert repo.password_updates == []


@pytest.mark.asyncio
async def test_reset_confirm_rejects_short_token_payload(
    client_and_repo: tuple[AsyncClient, FakeUserRepository],
) -> None:
    """Malformed (too-short) token payloads are rejected at the boundary."""
    client, repo = client_and_repo
    response = await client.post(
        "/api/v1/auth/password/reset-confirm",
        json={"token": "short", "new_password": "NewPass123"},
    )
    assert response.status_code == 422
    assert repo.password_updates == []


@pytest.mark.asyncio
async def test_service_raises_invalid_reset_token_for_consumed_token() -> None:
    """Service-level guard: consumed tokens raise InvalidResetTokenError."""
    repo = FakeUserRepository(
        user={"id": USER_ID, "email": USER_EMAIL, "password_hash": "h", "is_active": True}
    )
    raw_token = "f" * 32
    repo.tokens[hash_verification_token(raw_token)] = {
        "id": "token-used",
        "user_id": USER_ID,
        "purpose": "password_reset",
        "token_hash": hash_verification_token(raw_token),
        "expires_at": datetime.now(tz=UTC) + timedelta(minutes=10),
        "consumed_at": datetime.now(tz=UTC) - timedelta(minutes=1),
        "attempt_count": 1,
    }
    service = AuthService(user_repo=repo)

    with pytest.raises(InvalidResetTokenError):
        await service.confirm_password_reset(raw_token=raw_token, new_password="NewPass123")

    assert repo.attempt_bumps == ["token-used"]
