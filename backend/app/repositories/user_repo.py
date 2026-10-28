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
