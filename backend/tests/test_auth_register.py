"""Unit tests for TASK-M1-02: email/password registration endpoint (US 2.1).

Collaborators are mocked at the repository boundary; no live PostgreSQL,
Redis, or MinIO is required (follows the test_health.py pattern).
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.auth import get_auth_service
from app.core.errors import register_exception_handlers
from app.core.security import verify_password
from app.main import app
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService, DuplicateEmailError


class FakeUserRepository(UserRepository):
    """In-memory test double: records calls, returns canned rows, no database.

    Inherits UserRepository only to satisfy the service's typing; the parent's
    session is deliberately unused.
    """

    def __init__(
        self,
        session: AsyncSession | None = None,
        existing_emails: set[str] | None = None,
    ) -> None:
        self.existing_emails = existing_emails or set()
        self.created: list[dict[str, Any]] = []

    async def email_exists(self, email: str) -> bool:
        return email in self.existing_emails

    async def create_registered_user(self, **kwargs: Any) -> dict[str, Any]:
        self.created.append(kwargs)
        return {
            "id": "11111111-1111-1111-1111-111111111111",
            "email": kwargs["email"],
            "is_email_verified": False,
        }


@pytest_asyncio.fixture
async def client_and_repo() -> AsyncGenerator[tuple[AsyncClient, FakeUserRepository], None]:
    repo = FakeUserRepository()

    async def override_get_auth_service() -> AuthService:
        return AuthService(user_repo=repo)

    app.dependency_overrides[get_auth_service] = override_get_auth_service
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, repo
    app.dependency_overrides.clear()


VALID_PAYLOAD = {
    "full_name": "Nguyen Van A",
    "email": "student@example.com",
    "password": "Passw0rd123",
}


@pytest.mark.asyncio
async def test_register_success_returns_201_and_safe_payload(
    client_and_repo: tuple[AsyncClient, FakeUserRepository],
) -> None:
    """Happy path: 201, success envelope, no password material in response."""
    client, repo = client_and_repo
    response = await client.post("/api/v1/auth/register", json=VALID_PAYLOAD)

    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["data"]["email"] == "student@example.com"
    assert body["data"]["is_email_verified"] is False
    assert "user_id" in body["data"]
    body_text = response.text
    assert VALID_PAYLOAD["password"] not in body_text
    assert "password_hash" not in body_text


@pytest.mark.asyncio
async def test_register_hashes_password_with_argon2id_and_stages_verification_token(
    client_and_repo: tuple[AsyncClient, FakeUserRepository],
) -> None:
    """Repository receives an Argon2id hash (verifiable) plus a hashed token."""
    client, repo = client_and_repo
    response = await client.post("/api/v1/auth/register", json=VALID_PAYLOAD)

    assert response.status_code == 201
    assert len(repo.created) == 1
    created = repo.created[0]
    assert created["email"] == "student@example.com"
    assert created["full_name"] == "Nguyen Van A"
    assert created["password_hash"] != VALID_PAYLOAD["password"]
    assert verify_password(VALID_PAYLOAD["password"], created["password_hash"])
    assert len(created["verification_token_hash"]) == 64
    int(created["verification_token_hash"], 16)  # SHA-256 hex digest
    assert created["verification_token_expires_at"] > datetime.now(tz=UTC)


@pytest.mark.asyncio
async def test_register_normalizes_email_to_lowercase(
    client_and_repo: tuple[AsyncClient, FakeUserRepository],
) -> None:
    """Emails are normalized (trimmed, lowercased) before persistence."""
    client, repo = client_and_repo
    payload = {**VALID_PAYLOAD, "email": "  Student@Example.COM "}
    response = await client.post("/api/v1/auth/register", json=payload)

    assert response.status_code == 201
    assert response.json()["data"]["email"] == "student@example.com"
    assert repo.created[0]["email"] == "student@example.com"


@pytest.mark.asyncio
async def test_register_duplicate_email_rejected_with_409_and_no_write(
    client_and_repo: tuple[AsyncClient, FakeUserRepository],
) -> None:
    """Duplicate email (case-insensitive) -> 409 envelope, nothing persisted."""
    client, repo = client_and_repo
    repo.existing_emails.add("student@example.com")
    payload = {**VALID_PAYLOAD, "email": "STUDENT@example.com"}
    response = await client.post("/api/v1/auth/register", json=payload)

    assert response.status_code == 409
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "HTTP_409"
    assert "already exists" in body["error"]["message"]
    assert repo.created == []


@pytest.mark.asyncio
async def test_register_duplicate_email_error_message_has_no_email_value(
    client_and_repo: tuple[AsyncClient, FakeUserRepository],
) -> None:
    """Service exception never carries the email value (no PII in logs)."""
    service = AuthService(user_repo=FakeUserRepository(existing_emails={"x@y.com"}))
    with pytest.raises(DuplicateEmailError) as exc_info:
        await service.register_user(
            email="x@y.com", password="Passw0rd123", full_name="X"
        )
    assert "x@y.com" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("email", "not-an-email"),
        ("email", "missing-at-sign.example.com"),
        ("email", "user@nodot"),
        ("email", "user @example.com"),
        ("password", "Sh0rt"),  # under 8 chars
        ("password", "alllettersonly"),  # no digit
        ("password", "12345678"),  # no letter
        ("full_name", "   "),  # blank after strip
        ("full_name", "x" * 151),  # over VARCHAR(150)
        ("email", "x" * 250 + "@example.com"),  # over 254 chars
    ],
)
@pytest.mark.asyncio
async def test_register_rejects_invalid_boundary_input(
    field: str,
    value: str,
    client_and_repo: tuple[AsyncClient, FakeUserRepository],
) -> None:
    """Boundary validation: 422 envelope, repository never called."""
    client, repo = client_and_repo
    payload = {**VALID_PAYLOAD, field: value}
    response = await client.post("/api/v1/auth/register", json=payload)

    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert repo.created == []


@pytest.mark.asyncio
async def test_register_rejects_missing_fields(
    client_and_repo: tuple[AsyncClient, FakeUserRepository],
) -> None:
    """Missing required fields -> 422 validation error envelope."""
    client, _ = client_and_repo
    for incomplete in (
        {k: v for k, v in VALID_PAYLOAD.items() if k != "email"},
        {k: v for k, v in VALID_PAYLOAD.items() if k != "password"},
        {k: v for k, v in VALID_PAYLOAD.items() if k != "full_name"},
    ):
        response = await client.post("/api/v1/auth/register", json=incomplete)
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_register_accepts_boundary_valid_passwords(
    client_and_repo: tuple[AsyncClient, FakeUserRepository],
) -> None:
    """Proposed policy floor is accepted: 8 chars with letter and digit."""
    client, repo = client_and_repo
    response = await client.post(
        "/api/v1/auth/register", json={**VALID_PAYLOAD, "password": "Abcdefg1"}
    )
    assert response.status_code == 201
    assert len(repo.created) == 1


@pytest.mark.asyncio
async def test_repository_failure_yields_500(
    client_and_repo: tuple[AsyncClient, FakeUserRepository],
) -> None:
    """Unexpected repository failure -> HTTP 500, server does not crash.

    The main app runs with debug=True in development, where Starlette renders
    a plaintext traceback by design; the standardized 500 envelope contract is
    verified separately in test_unhandled_exception_returns_envelope_when_debug_disabled.
    """
    _, repo = client_and_repo

    async def broken_create(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("database exploded")

    repo.create_registered_user = broken_create  # type: ignore[method-assign]
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/auth/register", json=VALID_PAYLOAD)

    assert response.status_code == 500


@pytest.mark.asyncio
async def test_unhandled_exception_returns_envelope_when_debug_disabled() -> None:
    """Generic Exception handler maps failures to the safe 500 envelope.

    Uses a minimal probe app with debug disabled (production posture): the
    internal error message must never reach the response body.
    """
    probe = FastAPI()
    register_exception_handlers(probe)

    @probe.get("/boom")
    async def boom() -> None:
        raise RuntimeError("database exploded: sensitive internal detail")

    transport = ASGITransport(app=probe, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/boom")

    assert response.status_code == 500
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INTERNAL_SERVER_ERROR"
    assert body["error"]["message"] == "An unexpected internal server error occurred."
    assert "sensitive internal detail" not in response.text
