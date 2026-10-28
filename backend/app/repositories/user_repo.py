"""User persistence: SQL access for users, user_profiles, user_roles, verification_tokens.

This repository is the only module in the registration flow that touches SQL
(architecture invariant: repositories own data access; routers and services
never execute SQL directly). The project has no ORM models yet, so statements
use SQLAlchemy Core ``text()`` with bound parameters against the applied
baseline schema (migration 0001_initial_baseline.py).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class UserRepository:
    """SQL access for the auth user aggregate. No business logic lives here."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def email_exists(self, email: str) -> bool:
        """Return True if a user with exactly this email is already registered."""
        async with self._session.begin():
            result = await self._session.execute(
                text("SELECT 1 FROM users WHERE email = :email LIMIT 1"),
                {"email": email},
            )
            found = result.scalar_one_or_none() is not None
        return found

    async def create_registered_user(
        self,
        *,
        email: str,
        password_hash: str,
        full_name: str,
        verification_token_hash: str,
        verification_token_expires_at: datetime,
        role_name: str = "student",
    ) -> dict[str, Any]:
        """Atomically create user + profile + default role + verification token.

        Runs in a single transaction so a partial registration can never
        persist. Returns the created user's public fields; the password hash is
        never included.
        """
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "INSERT INTO users (email, password_hash) "
                    "VALUES (:email, :password_hash) "
                    "RETURNING id, email, is_email_verified"
                ),
                {"email": email, "password_hash": password_hash},
            )
            user_row = result.mappings().one()

            await self._session.execute(
                text(
                    "INSERT INTO user_profiles (user_id, full_name) "
                    "VALUES (:user_id, :full_name)"
                ),
                {"user_id": user_row["id"], "full_name": full_name},
            )

            role_result = await self._session.execute(
                text("SELECT id FROM roles WHERE name = :name"),
                {"name": role_name},
            )
            role_id = role_result.scalar_one_or_none()
            if role_id is None:
                raise ValueError(f"Required role is missing from the seeded roles: {role_name}")

            await self._session.execute(
                text(
                    "INSERT INTO user_roles (user_id, role_id) "
                    "VALUES (:user_id, :role_id)"
                ),
                {"user_id": user_row["id"], "role_id": role_id},
            )

            await self._session.execute(
                text(
                    "INSERT INTO verification_tokens (user_id, purpose, token_hash, expires_at) "
                    "VALUES (:user_id, 'email_verification', :token_hash, :expires_at)"
                ),
                {
                    "user_id": user_row["id"],
                    "token_hash": verification_token_hash,
                    "expires_at": verification_token_expires_at,
                },
            )

        return {
            "id": str(user_row["id"]),
            "email": user_row["email"],
            "is_email_verified": user_row["is_email_verified"],
        }

    async def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        """Fetch a user's auth-relevant fields by exact (normalized) email."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT id, email, password_hash, is_active, is_email_verified "
                    "FROM users WHERE email = :email"
                ),
                {"email": email},
            )
            row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def update_last_login(self, user_id: str) -> None:
        """Record a successful login timestamp."""
        async with self._session.begin():
            await self._session.execute(
                text("UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = :user_id"),
                {"user_id": user_id},
            )

    async def create_refresh_session(
        self,
        *,
        user_id: str,
        token_hash: str,
        expires_at: datetime,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> str:
        """Persist a refresh session keyed by the hashed refresh token."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "INSERT INTO refresh_sessions (user_id, token_hash, expires_at, user_agent, ip_address) "
                    "VALUES (:user_id, :token_hash, :expires_at, :user_agent, :ip_address) "
                    "RETURNING id"
                ),
                {
                    "user_id": user_id,
                    "token_hash": token_hash,
                    "expires_at": expires_at,
                    "user_agent": user_agent,
                    "ip_address": ip_address,
                },
            )
            session_id = str(result.scalar_one())
        return session_id

    async def get_refresh_session_by_hash(self, token_hash: str) -> dict[str, Any] | None:
        """Fetch a refresh session (with the owner's active flag) by token hash."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT rs.id, rs.user_id, rs.expires_at, rs.revoked_at, "
                    "u.is_active AS user_is_active "
                    "FROM refresh_sessions rs "
                    "JOIN users u ON u.id = rs.user_id "
                    "WHERE rs.token_hash = :token_hash"
                ),
                {"token_hash": token_hash},
            )
            row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def revoke_refresh_session(self, session_id: str) -> None:
        """Mark a refresh session revoked (no-op when already revoked)."""
        async with self._session.begin():
            await self._session.execute(
                text(
                    "UPDATE refresh_sessions SET revoked_at = CURRENT_TIMESTAMP "
                    "WHERE id = :session_id AND revoked_at IS NULL"
                ),
                {"session_id": session_id},
            )

    async def touch_refresh_session(self, session_id: str) -> None:
        """Update the session's last-used timestamp."""
        async with self._session.begin():
            await self._session.execute(
                text(
                    "UPDATE refresh_sessions SET last_used_at = CURRENT_TIMESTAMP "
                    "WHERE id = :session_id"
                ),
                {"session_id": session_id},
            )
