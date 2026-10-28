"""Authentication business logic: registration (US 2.1). Login/JWT follows in TASK-M1-03.

Layering: the service orchestrates repositories and security primitives; it
never executes SQL and never touches HTTP concerns (status codes, envelopes).
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import settings
from app.core.security import (
    InvalidTokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_verification_token,
    hash_password,
    hash_verification_token,
    verify_password,
)
from app.repositories.audit_repo import AuditRepository
from app.repositories.user_repo import UserRepository

# Proposed default, pending Product Owner approval: an email-verification
# token stays valid for 24 hours after registration. US 2.3 specifies 15
# minutes for password-reset codes but no document defines this value for
# email verification.
VERIFICATION_TOKEN_TTL_HOURS = 24

# US 2.3: "time-limited code/link (e.g., 15 min), single use".
PASSWORD_RESET_TOKEN_TTL_MINUTES = 15


class DuplicateEmailError(Exception):
    """Raised when registration attempts to reuse an already-registered email.

    Message intentionally carries no email value (PII must not leak into logs).
    """


class InvalidCredentialsError(Exception):
    """Raised for any failed authentication (bad email, password, or session).

    A single exception keeps every failure mapped to the same generic response
    (US 2.2: never reveal which part was wrong).
    """


class AccountSuspendedError(Exception):
    """Raised when a deactivated (suspended) account attempts to log in."""


class InvalidResetTokenError(Exception):
    """Raised when a password-reset token is unknown, expired, or consumed."""


class TooManyAttemptsError(Exception):
    """Raised when login attempts exceed the NFR04 threshold for an identifier."""


class AuthService:
    """Auth use cases. Collaborators are injected for testability.

    ``audit_repo`` powers NFR04 login-attempt limiting and security-event
    logging; it is optional so legacy constructions (older tests) keep working,
    while the composed production path always provides it (fail-closed by
    wiring, not by omission).
    """

    def __init__(
        self,
        user_repo: UserRepository,
        audit_repo: AuditRepository | None = None,
    ) -> None:
        self._user_repo = user_repo
        self._audit_repo = audit_repo

    @classmethod
    def from_session(cls, session: Any) -> "AuthService":
        """Compose the service with its repositories over one session.

        Keeps the service as the only repository consumer so routers depend
        on services exclusively (architecture invariant: routers never import
        repositories).
        """
        return cls(user_repo=UserRepository(session), audit_repo=AuditRepository(session))

    async def _enforce_login_rate_limit(self, *, identifier_hash: str) -> None:
        """Raise TooManyAttemptsError when the identifier is locked out (NFR04).

        Lockout is anchored at the threshold-crossing failure: rejections
        during a lockout record nothing, so the window cannot be refreshed by
        the attacker (no self-extended DoS of the legitimate account).
        """
        if self._audit_repo is None:
            return
        state = await self._audit_repo.get_failure_state(
            identifier_hash, settings.login_window_minutes
        )
        failures = int(state["failures"])
        last_failure_at = state["last_failure_at"]
        if failures < settings.login_max_failed_attempts or last_failure_at is None:
            return
        unlock_at = last_failure_at + timedelta(minutes=settings.login_lockout_minutes)
        if datetime.now(tz=last_failure_at.tzinfo) < unlock_at:
            raise TooManyAttemptsError("Too many failed attempts. Try again later.")

    async def _record_failed_login(
        self,
        *,
        identifier_hash: str,
        actor_user_id: str | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        """Record one failed attempt as evidence and to the audit trail."""
        if self._audit_repo is None:
            return
        await self._audit_repo.record_attempt(
            identifier_hash=identifier_hash, ip_address=ip_address, succeeded=False
        )
        await self._audit_repo.record_security_event(
            event_type="login_failed",
            severity="warning",
            actor_user_id=actor_user_id,
            ip_address=ip_address,
            user_agent=user_agent,
            details={"reason": "invalid_credentials"},
        )
        state = await self._audit_repo.get_failure_state(
            identifier_hash, settings.login_window_minutes
        )
        if int(state["failures"]) == settings.login_max_failed_attempts:
            await self._audit_repo.record_security_event(
                event_type="login_locked",
                severity="error",
                actor_user_id=actor_user_id,
                ip_address=ip_address,
                user_agent=user_agent,
                details={"reason": "attempt_threshold_reached"},
            )

    async def register_user(self, *, email: str, password: str, full_name: str) -> dict[str, Any]:
        """Register a new student account and stage an email-verification token.

        The password is hashed with Argon2id and never persisted or returned in
        plaintext. The raw verification token is intentionally not returned or
        stored: only its SHA-256 hash is persisted, and the raw value belongs to
        the verification email (delivery via the Email Sender/Celery worker is a
        later milestone and is out of scope here).
        """
        normalized_email = email.strip().lower()
        if await self._user_repo.email_exists(normalized_email):
            raise DuplicateEmailError("Email is already registered.")

        password_hash = hash_password(password)
        _, token_hash = generate_verification_token()
        expires_at = datetime.now(tz=UTC) + timedelta(hours=VERIFICATION_TOKEN_TTL_HOURS)

        user = await self._user_repo.create_registered_user(
            email=normalized_email,
            password_hash=password_hash,
            full_name=full_name.strip(),
            verification_token_hash=token_hash,
            verification_token_expires_at=expires_at,
        )

        return {
            "user_id": user["id"],
            "email": user["email"],
            "is_email_verified": user["is_email_verified"],
        }

    async def login_user(
        self,
        *,
        email: str,
        password: str,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        """Authenticate with email/password and issue an access + refresh token pair.

        Failures raise :class:`InvalidCredentialsError` for both unknown emails
        and wrong passwords (generic response, US 2.2). Suspended accounts are
        rejected only after credentials verify (no account-status oracle).
        Only the SHA-256 hash of the refresh token is persisted.
        """
        normalized_email = email.strip().lower()
        identifier_hash = hash_verification_token(normalized_email)
        await self._enforce_login_rate_limit(identifier_hash=identifier_hash)

        user = await self._user_repo.get_user_by_email(normalized_email)
        if user is None or not verify_password(password, user["password_hash"]):
            await self._record_failed_login(
                identifier_hash=identifier_hash,
                actor_user_id=str(user["id"]) if user else None,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            raise InvalidCredentialsError("Invalid email or password.")
        if not user["is_active"]:
            raise AccountSuspendedError("This account has been suspended.")

        access_token = create_access_token(str(user["id"]))
        refresh_token = create_refresh_token(str(user["id"]))
        expires_at = datetime.now(tz=UTC) + timedelta(days=settings.refresh_token_expire_days)

        await self._user_repo.create_refresh_session(
            user_id=str(user["id"]),
            token_hash=hash_verification_token(refresh_token),
            expires_at=expires_at,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        await self._user_repo.update_last_login(str(user["id"]))
        if self._audit_repo is not None:
            await self._audit_repo.record_attempt(
                identifier_hash=identifier_hash, ip_address=ip_address, succeeded=True
            )

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": settings.access_token_expire_minutes * 60,
        }

    async def refresh_access_token(self, *, refresh_token: str) -> dict[str, Any]:
        """Issue a new access token from a valid, unrevoked refresh session.

        The refresh token itself is not rotated (no document requires rotation);
        it stays valid until expiry or explicit revocation. Proposed default,
        pending Product Owner approval.
        """
        try:
            claims = decode_token(refresh_token, expected_type="refresh")
        except InvalidTokenError as exc:
            raise InvalidCredentialsError("Invalid or expired session.") from exc

        session_row = await self._user_repo.get_refresh_session_by_hash(
            hash_verification_token(refresh_token)
        )
        if session_row is None or session_row["revoked_at"] is not None:
            raise InvalidCredentialsError("Invalid or expired session.")
        if not session_row["user_is_active"]:
            raise InvalidCredentialsError("Invalid or expired session.")

        await self._user_repo.touch_refresh_session(str(session_row["id"]))
        return {
            "access_token": create_access_token(str(claims["sub"])),
            "token_type": "bearer",
            "expires_in": settings.access_token_expire_minutes * 60,
        }

    async def logout_session(self, *, refresh_token: str) -> None:
        """Revoke the refresh session for this token. Idempotent.

        Unknown or malformed tokens are silently accepted (200) so logout never
        leaks whether a session existed.
        """
        try:
            decode_token(refresh_token, expected_type="refresh")
        except InvalidTokenError:
            return

        session_row = await self._user_repo.get_refresh_session_by_hash(
            hash_verification_token(refresh_token)
        )
        if session_row is not None and session_row["revoked_at"] is None:
            await self._user_repo.revoke_refresh_session(str(session_row["id"]))

    async def request_password_reset(self, *, email: str) -> None:
        """Stage a single-use password-reset token for the account, if it exists.

        The response is deliberately identical whether or not the email is
        registered (no user enumeration, US 2.3/2.2). The raw token belongs to
        the reset email (delivery deferred to the worker milestone).
        """
        normalized_email = email.strip().lower()
        user = await self._user_repo.get_user_by_email(normalized_email)
        if user is None:
            return

        _, token_hash = generate_verification_token()
        expires_at = datetime.now(tz=UTC) + timedelta(minutes=PASSWORD_RESET_TOKEN_TTL_MINUTES)
        await self._user_repo.create_verification_token(
            user_id=str(user["id"]),
            purpose="password_reset",
            token_hash=token_hash,
            expires_at=expires_at,
        )

    async def confirm_password_reset(self, *, raw_token: str, new_password: str) -> None:
        """Consume a password-reset token and rotate the password.

        On success the password is re-hashed with Argon2id and EVERY active
        refresh session for the user is revoked (US 2.3). Invalid, expired, or
        consumed tokens raise the same generic error; each failed attempt on a
        known token increments its attempt_count.
        """
        token_hash = hash_verification_token(raw_token)
        token_row = await self._user_repo.get_verification_token_by_hash(
            token_hash, purpose="password_reset"
        )
        if token_row is None:
            raise InvalidResetTokenError("Invalid or expired reset link.")

        now = datetime.now(tz=UTC)
        if token_row["consumed_at"] is not None or token_row["expires_at"] <= now:
            await self._user_repo.increment_token_attempts(str(token_row["id"]))
            raise InvalidResetTokenError("Invalid or expired reset link.")

        password_hash = hash_password(new_password)
        await self._user_repo.update_user_password(str(token_row["user_id"]), password_hash)
        await self._user_repo.consume_verification_token(str(token_row["id"]))
        await self._user_repo.revoke_all_refresh_sessions(str(token_row["user_id"]))
