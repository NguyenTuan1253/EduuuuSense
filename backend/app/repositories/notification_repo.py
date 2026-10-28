"""Notification + reminder-evidence persistence (US 2.19/2.20, Invariant 12).

The notifications module READS/DISPLAYS/marks-read; WRITES come from the
Worker (reminders), Progress, and the API Backend only. deduplication_key is
the duplicate-send defense for retried scans.
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class NotificationRepository:
    """SQL access for notifications. No business logic lives here."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_notification(
        self,
        *,
        user_id: str,
        notification_type: str,
        category: str,
        title: str,
        content: str,
        channel: str = "in_app",
        target_url: str | None = None,
        deduplication_key: str | None = None,
    ) -> dict[str, Any] | None:
        """Insert one notification; returns the existing row on duplicate key."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "INSERT INTO notifications (user_id, notification_type, category, title, "
                    "content, channel, target_url, deduplication_key) "
                    "VALUES (:user_id, :notification_type, :category, :title, :content, "
                    ":channel, :target_url, :deduplication_key) "
                    "ON CONFLICT (deduplication_key) DO NOTHING "
                    "RETURNING id, user_id, notification_type, category, title, content, "
                    "channel, target_url, is_read, status, created_at"
                ),
                {
                    "user_id": user_id,
                    "notification_type": notification_type,
                    "category": category,
                    "title": title,
                    "content": content,
                    "channel": channel,
                    "target_url": target_url,
                    "deduplication_key": deduplication_key,
                },
            )
            row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def list_notifications(
        self, user_id: str, *, unread_only: bool = False, limit: int = 50
    ) -> list[dict[str, Any]]:
        clauses = "user_id = :user_id" + (" AND is_read = FALSE" if unread_only else "")
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    f"SELECT id, user_id, notification_type, category, title, content, "
                    f"channel, target_url, is_read, status, created_at "
                    f"FROM notifications WHERE {clauses} "
                    f"ORDER BY created_at DESC LIMIT :limit"
                ),
                {"user_id": user_id, "limit": limit},
            )
            rows = result.mappings().all()
        return [dict(row) for row in rows]

    async def mark_read(self, user_id: str, notification_id: str) -> bool:
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "UPDATE notifications SET is_read = TRUE, read_at = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = :notification_id AND user_id = :user_id AND is_read = FALSE"
                ),
                {"notification_id": notification_id, "user_id": user_id},
            )
            marked = int(getattr(result, "rowcount", 0) or 0) > 0
        return marked

    async def mark_sent(self, notification_id: str) -> bool:
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "UPDATE notifications SET status = 'sent', sent_at = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = :notification_id"
                ),
                {"notification_id": notification_id},
            )
            marked = int(getattr(result, "rowcount", 0) or 0) > 0
        return marked

    async def mark_failed(self, notification_id: str, error_code: str) -> bool:
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "UPDATE notifications SET status = 'failed', "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = :notification_id"
                ),
                {"notification_id": notification_id},
            )
            marked = int(getattr(result, "rowcount", 0) or 0) > 0
        return marked

    # ---- reminder scan inputs (owner-scoped) --------------------------------

    async def assignments_due_between(
        self, user_id: str, start: str, end: str
    ) -> list[dict[str, Any]]:
        """Open assignments of the owner due in [start, end)."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT a.id, a.course_id, a.title, a.due_at, a.priority, u.email AS owner_email "
                    "FROM assignments a JOIN courses c ON c.id = a.course_id "
                    "JOIN users u ON u.id = c.user_id "
                    "WHERE c.user_id = :user_id AND a.status <> 'completed' "
                    "AND a.due_at IS NOT NULL AND a.due_at >= :start AND a.due_at < :end"
                ),
                {"user_id": user_id, "start": start, "end": end},
            )
            rows = result.mappings().all()
        return [dict(row) for row in rows]

    async def exams_starting_between(
        self, user_id: str, start: str, end: str
    ) -> list[dict[str, Any]]:
        """Exams of the owner starting in [start, end)."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT e.id, e.course_id, e.title, e.exam_at, e.location, u.email AS owner_email "
                    "FROM exams e JOIN courses c ON c.id = e.course_id "
                    "JOIN users u ON u.id = c.user_id "
                    "WHERE c.user_id = :user_id AND e.exam_at >= :start AND e.exam_at < :end"
                ),
                {"user_id": user_id, "start": start, "end": end},
            )
            rows = result.mappings().all()
        return [dict(row) for row in rows]

    async def list_users_with_soon_due_assignments(self, window_start: str, window_end: str) -> list[tuple[str, str]]:
        """Distinct (user_id, email) owners having open assignments in the window."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT DISTINCT c.user_id, u.email "
                    "FROM assignments a JOIN courses c ON c.id = a.course_id "
                    "JOIN users u ON u.id = c.user_id "
                    "WHERE a.status <> 'completed' AND a.due_at IS NOT NULL "
                    "AND a.due_at >= :start AND a.due_at < :end"
                ),
                {"start": window_start, "end": window_end},
            )
            rows = [(str(r0), str(r1)) for r0, r1 in result.all()]
        return rows

    async def list_users_with_upcoming_exams(self, window_start: str, window_end: str) -> list[tuple[str, str]]:
        """Distinct (user_id, email) owners having exams in the window."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT DISTINCT c.user_id, u.email "
                    "FROM exams e JOIN courses c ON c.id = e.course_id "
                    "JOIN users u ON u.id = c.user_id "
                    "WHERE e.exam_at >= :start AND e.exam_at < :end"
                ),
                {"start": window_start, "end": window_end},
            )
            rows = [(str(r0), str(r1)) for r0, r1 in result.all()]
        return rows
