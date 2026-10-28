"""Unit tests for TASK-M1-06: login attempt limiting & audit logging (NFR04)."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.auth import get_auth_service
from app.core.security import hash_password
from app.main import app
from app.repositories.audit_repo import AuditRepository
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService

USER_ID = "55555555-5555-5555-5555-555555555555"
USER_EMAIL = "student@example.com"
MAX_ATTEMPTS = 5


class FakeAuditRepository(AuditRepository):
    """In-memory rate-limit evidence and audit trail."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self.attempts: list[dict[str, Any]] = []  # identifier_hash, ip, succeeded, attempted_at
        self.events: list[dict[str, Any]] = []

    async def get_failure_state(self, identifier_hash: str, window_minutes: int) -> dict[str, Any]:
        now = datetime.now(tz=UTC)
        cutoff = now - timedelta(minutes=window_minutes)
        success_rows = [
            a for a in self.attempts if a["identifier_hash"] == identifier_hash and a["succeeded"]
        ]
        last_success = max((a["attempted_at"] for a in success_rows), default=None)
        failures = [
            a
            for a in self.attempts
            if a["identifier_hash"] == identifier_hash
            and not a["succeeded"]
            and a["attempted_at"] > cutoff
            and (last_success is None or a["attempted_at"] > last_success)
        ]
        last_failure = max((a["attempted_at"] for a in failures), default=None)
        return {"failures": len(failures), "last_failure_at": last_failure}

    async def record_attempt(self, *, identifier_hash: str, ip_address: str | None, succeeded: bool) -> None:
        self.attempts.append(
            {
                "identifier_hash": identifier_hash,
                "ip_address": ip_address,
                "succeeded": succeeded,
                "attempted_at": datetime.now(tz=UTC),
            }
        )

    async def record_security_event(self, **kwargs: Any) -> None:
        self.events.append(kwargs)

    async def get_failure_state_since(self, identifier_hash: str, window_minutes: int, since: datetime) -> dict[str, Any]:
        raise NotImplementedError("unused in unit tests")


class FakeUserRepository(UserRepository):
    def __init__(self, session: AsyncSession | None = None) -> None:
        self.user = {
            "id": USER_ID,
            "email": USER_EMAIL,
            "password_hash": hash_password("Passw0rd123"),
            "is_active": True,
            "is_email_verified": True,
        }

    async def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        return dict(self.user) if self.user["email"] == email else None

    async def create_refresh_session(self, **kwargs: Any) -> str:
        return "session-1"

    async def update_last_login(self, user_id: str) -> None:
        return None


@pytest_asyncio.fixture
async def client_and_repos() -> AsyncGenerator[tuple[AsyncClient, FakeUserRepository, FakeAuditRepository], None]:
    user_repo = FakeUserRepository()
    audit_repo = FakeAuditRepository()

    async def override_get_auth_service() -> AuthService:
        return AuthService(user_repo=user_repo, audit_repo=audit_repo)

    app.dependency_overrides[get_auth_service] = override_get_auth_service
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, user_repo, audit_repo
    app.dependency_overrides.clear()


async def _fail_login(client: AsyncClient, email: str = USER_EMAIL, password: str = "WrongPass1") -> int:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    return response.status_code


@pytest.mark.asyncio
async def test_failures_below_threshold_return_401_and_are_audited(
    client_and_repos: tuple[AsyncClient, FakeUserRepository, FakeAuditRepository],
) -> None:
    """(MAX-1) failures: generic 401 each; every failure audited as warning."""
    client, _, audit = client_and_repos
    codes = [await _fail_login(client) for _ in range(MAX_ATTEMPTS - 1)]

    assert codes == [401] * (MAX_ATTEMPTS - 1)
    failed_events = [e for e in audit.events if e["event_type"] == "login_failed"]
    assert len(failed_events) == MAX_ATTEMPTS - 1
    assert all(e["severity"] == "warning" for e in failed_events)
    assert not any(e["event_type"] == "login_locked" for e in audit.events)


@pytest.mark.asyncio
async def test_threshold_crossing_failure_locks_account(
    client_and_repos: tuple[AsyncClient, FakeUserRepository, FakeAuditRepository],
) -> None:
    """The MAX-th failure triggers a login_locked audit event (severity error)."""
    client, _, audit = client_and_repos
    for _ in range(MAX_ATTEMPTS):
        await _fail_login(client)

    locked_events = [e for e in audit.events if e["event_type"] == "login_locked"]
    assert len(locked_events) == 1
    assert locked_events[0]["severity"] == "error"


@pytest.mark.asyncio
async def test_locked_identifier_rejects_even_correct_password(
    client_and_repos: tuple[AsyncClient, FakeUserRepository, FakeAuditRepository],
) -> None:
    """After lockout, even the CORRECT password gets 429 (limit precedes check)."""
    client, _, audit = client_and_repos
    for _ in range(MAX_ATTEMPTS):
        await _fail_login(client)

    response = await client.post(
        "/api/v1/auth/login", json={"email": USER_EMAIL, "password": "Passw0rd123"}
    )

    assert response.status_code == 429
    assert response.json()["error"]["message"] == "Too many failed attempts. Try again later."


@pytest.mark.asyncio
async def test_lockout_rejections_do_not_extend_the_window(
    client_and_repos: tuple[AsyncClient, FakeUserRepository, FakeAuditRepository],
) -> None:
    """Attempts during lockout record nothing (no self-extended DoS)."""
    client, _, audit = client_and_repos
    for _ in range(MAX_ATTEMPTS):
        await _fail_login(client)
    attempts_at_lock = len(audit.attempts)
    events_at_lock = len(audit.events)

    for _ in range(3):
        locked = await client.post(
            "/api/v1/auth/login", json={"email": USER_EMAIL, "password": "WrongPass1"}
        )
        assert locked.status_code == 429

    assert len(audit.attempts) == attempts_at_lock
    assert len(audit.events) == events_at_lock


@pytest.mark.asyncio
async def test_successful_login_resets_the_failure_counter(
    client_and_repos: tuple[AsyncClient, FakeUserRepository, FakeAuditRepository],
) -> None:
    """Failures after a success start counting from zero again."""
    client, _, audit = client_and_repos
    for _ in range(MAX_ATTEMPTS - 1):
        await _fail_login(client)
    success = await client.post(
        "/api/v1/auth/login", json={"email": USER_EMAIL, "password": "Passw0rd123"}
    )
    assert success.status_code == 200

    codes = [await _fail_login(client) for _ in range(MAX_ATTEMPTS - 1)]

    assert codes == [401] * (MAX_ATTEMPTS - 1)  # not locked yet
    assert not any(e["event_type"] == "login_locked" for e in audit.events)


@pytest.mark.asyncio
async def test_window_expiry_unlocks_identifier(
    client_and_repos: tuple[AsyncClient, FakeUserRepository, FakeAuditRepository],
) -> None:
    """Failures older than the window no longer count toward lockout."""
    client, _, audit = client_and_repos
    for _ in range(MAX_ATTEMPTS):
        await _fail_login(client)

    # Age every recorded failure beyond the window.
    old = datetime.now(tz=UTC) - timedelta(minutes=30)
    for attempt in audit.attempts:
        attempt["attempted_at"] = old

    response = await client.post(
        "/api/v1/auth/login", json={"email": USER_EMAIL, "password": "WrongPass1"}
    )
    assert response.status_code == 401  # not 429


@pytest.mark.asyncio
async def test_identifiers_are_stored_hashed_not_plaintext(
    client_and_repos: tuple[AsyncClient, FakeUserRepository, FakeAuditRepository],
) -> None:
    """Evidence stores SHA-256 identifier hashes; the email never appears."""
    client, _, audit = client_and_repos
    await _fail_login(client)

    assert audit.attempts
    assert all(USER_EMAIL != a["identifier_hash"] for a in audit.attempts)
    assert all(len(a["identifier_hash"]) == 64 for a in audit.attempts)


@pytest.mark.asyncio
async def test_audit_events_never_carry_secrets(
    client_and_repos: tuple[AsyncClient, FakeUserRepository, FakeAuditRepository],
) -> None:
    """No passwords or token material in audit payloads."""
    client, _, audit = client_and_repos
    await _fail_login(client, password="WrongPass1")

    for event in audit.events:
        blob = str(event.get("details", {})) + str(event.get("event_type", ""))
        assert "WrongPass1" not in blob
        assert event["details"] == {"reason": "invalid_credentials"}


@pytest.mark.asyncio
async def test_different_identifiers_are_isolated(
    client_and_repos: tuple[AsyncClient, FakeUserRepository, FakeAuditRepository],
) -> None:
    """Lockout is per identifier; another account is unaffected."""
    client, _, audit = client_and_repos
    for _ in range(MAX_ATTEMPTS):
        await _fail_login(client, email="other@example.com")

    response = await client.post(
        "/api/v1/auth/login", json={"email": USER_EMAIL, "password": "Passw0rd123"}
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_service_without_audit_repo_still_logs_in(
    client_and_repos: tuple[AsyncClient, FakeUserRepository, FakeAuditRepository],
) -> None:
    """Legacy construction (audit_repo=None) keeps working (compat seam)."""
    _, user_repo, _ = client_and_repos
    service = AuthService(user_repo=user_repo, audit_repo=None)
    tokens = await service.login_user(email=USER_EMAIL, password="Passw0rd123")

    assert tokens["access_token"]
    assert tokens["refresh_token"]
