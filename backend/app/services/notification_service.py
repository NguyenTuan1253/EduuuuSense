"""Notification module (US 2.20, Invariant 12): reads/displays/marks-read ONLY.

This service never CREATES notifications — writers are the Worker (reminders),
Progress, and the API Backend per the architecture. The API surface is:
list (all/unread), unread count, and mark-read.
"""

from typing import Any

from app.repositories.notification_repo import NotificationRepository


class NotificationNotFoundError(Exception):
    """Raised when a notification does not exist for the requesting owner."""


class NotificationService:
    """Read-side notification use cases for the authenticated owner."""

    def __init__(self, notification_repo: NotificationRepository) -> None:
        self._notification_repo = notification_repo

    @classmethod
    def from_session(cls, session: Any) -> "NotificationService":
        """Compose over one session."""
        return cls(notification_repo=NotificationRepository(session))

    async def list_notifications(
        self, user_id: str, *, unread_only: bool = False, limit: int = 50
    ) -> dict[str, Any]:
        rows = await self._notification_repo.list_notifications(
            user_id, unread_only=unread_only, limit=min(limit, 100)
        )
        unread = await self._notification_repo.list_notifications(user_id, unread_only=True, limit=100)
        return {
            "notifications": rows,
            "unread_count": len(unread),
        }

    async def mark_read(self, user_id: str, notification_id: str) -> dict[str, Any]:
        marked = await self._notification_repo.mark_read(user_id, notification_id)
        if not marked:
            raise NotificationNotFoundError("Notification not found or already read.")
        return {"id": notification_id, "is_read": True}
