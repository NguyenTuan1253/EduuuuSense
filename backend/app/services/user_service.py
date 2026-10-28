"""User profile business logic (US 2.4) and the shared JWT auth dependency.

The ``get_current_user_id`` dependency lives here (service layer) so routers
depend only on services; it is the single authentication gate reused by every
protected endpoint in the backend.
"""

from typing import Any, Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import InvalidTokenError, decode_token
from app.repositories.user_repo import UserRepository

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user_id(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> str:
    """Require a valid access JWT; return the authenticated user's id.

    Every protected endpoint must depend on this (or an admin variant built on
    it). The subject comes exclusively from the signed token — client-supplied
    user ids are never trusted (ownership invariant).
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = decode_token(credentials.credentials, expected_type="access")
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return str(claims["sub"])


class UserService:
    """Profile use cases for the authenticated student (owner-only)."""

    def __init__(self, user_repo: UserRepository) -> None:
        self._user_repo = user_repo

    @classmethod
    def from_session(cls, session: Any) -> "UserService":
        """Compose the service with its repository over one session."""
        return cls(user_repo=UserRepository(session))

    async def get_own_profile(self, user_id: str) -> dict[str, Any]:
        """Return the authenticated user's account and profile information."""
        row = await self._user_repo.get_user_with_profile(user_id)
        if row is None:
            # Token subject no longer exists or was deleted server-side.
            raise PermissionError("Authenticated user no longer exists.")
        return row

    async def update_own_profile(self, user_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        """Apply the explicitly provided profile changes for the owner only."""
        if not changes:
            raise ValueError("No profile fields provided.")
        return await self._user_repo.update_user_profile(user_id, changes)

    async def get_current_plan_code(self, user_id: str) -> str:
        """Plan code for quota decisions; unknown subjects default to free."""
        row = await self._user_repo.get_user_with_profile(user_id)
        return str(row["current_plan_code"]) if row else "free"

async def require_admin(
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> str:
    """RBAC gate: only System Administrators pass (US 2.5: 403, no details)."""
    from fastapi import HTTPException
    from fastapi import status as http_status

    from app.core.database import async_session_factory

    async with async_session_factory() as session:
        service = AdminUserService.from_session(session)
        has_role = await service.user_has_role(user_id, "admin")
    if not has_role:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Forbidden.",
        )
    return user_id


class AdminUserService:
    """Admin account management (US 2.22)."""

    def __init__(self, user_repo: UserRepository) -> None:
        self._user_repo = user_repo

    @classmethod
    def from_session(cls, session: Any) -> "AdminUserService":
        """Compose over one session."""
        return cls(user_repo=UserRepository(session))

    async def user_has_role(self, user_id: str, role_name: str) -> bool:
        return await self._user_repo.user_has_role(user_id, role_name)

    async def list_accounts(
        self, *, search: str | None = None, is_active: bool | None = None, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        return await self._user_repo.list_accounts(
            search=search, is_active=is_active, limit=min(limit, 100), offset=max(offset, 0)
        )

    async def set_account_active(self, admin_id: str, user_id: str, is_active: bool) -> dict[str, Any]:
        """Suspend/activate; suspending also revokes all sessions (security)."""
        updated = await self._user_repo.set_account_active(user_id, is_active)
        if not updated:
            raise LookupError("Account not found.")
        revoked = 0
        if not is_active:
            revoked = await self._user_repo.revoke_all_refresh_sessions(user_id)
        return {"user_id": user_id, "is_active": is_active, "sessions_revoked": revoked}
