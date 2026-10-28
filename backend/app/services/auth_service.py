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
from app.repositories.user_repo import UserRepository

# Proposed default, pending Product Owner approval: an email-verification
# token stays valid for 24 hours after registration. US 2.3 specifies 15
# minutes for password-reset codes but no document defines this value for
# email verification.
VERIFICATION_TOKEN_TTL_HOURS = 24


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


class AuthService:
    """Auth use cases. Collaborators are injected for testability."""

    def __init__(self, user_repo: UserRepository) -> None:
        self._user_repo = user_repo

    @classmethod
    def from_session(cls, session: Any) -> "AuthService":
        """Compose the service with its repository over one session.

        Keeps the service as the only repository consumer so routers depend
        on services exclusively (architecture invariant: routers never import
        repositories).
        """
        return cls(user_repo=UserRepository(session))

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
        user = await self._user_repo.get_user_by_email(normalized_email)
        if user is None or not verify_password(password, user["password_hash"]):
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
