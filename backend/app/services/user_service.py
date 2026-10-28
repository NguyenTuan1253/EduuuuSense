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
