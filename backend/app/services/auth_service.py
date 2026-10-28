"""Authentication business logic: registration (US 2.1). Login/JWT follows in TASK-M1-03.

Layering: the service orchestrates repositories and security primitives; it
never executes SQL and never touches HTTP concerns (status codes, envelopes).
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.security import generate_verification_token, hash_password
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
