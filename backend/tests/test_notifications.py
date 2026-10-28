"""Unit tests for TASK-M5-09: notifications API (US 2.20, Invariant 12)."""

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.v1.notifications import get_notification_service
from app.core.security import create_access_token
from app.main import app
from app.repositories.notification_repo import NotificationRepository
from app.services.notification_service import NotificationService

USER_ID = "n1n1n1n1-n1n1-n1n1-n1n1-n1n1n1n1n1n1"
OTHER_ID = "n2n2n2n2-n2n2-n2n2-n2n2-n2n2n2n2n2n2"


class FakeNotificationRepository(NotificationRepository):
    def __init__(self, session: Any = None) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def seed(self, notification_id: str, *, is_read: bool = False) -> None:
        self.rows[notification_id] = {
            "id": notification_id,
            "user_id": USER_ID,
            "notification_type": "deadline_reminder",
            "category": "assignments",
            "title": f"N-{notification_id}",
            "content": "body",
            "channel": "in_app",
            "is_read": is_read,
            "status": "sent",
            "created_at": "2026-10-28T00:00:00+00:00",
        }

    async def list_notifications(self, user_id: str, *, unread_only: bool = False, limit: int = 50) -> list[dict[str, Any]]:
        rows = [n for n in self.rows.values() if n["user_id"] == user_id]
        if unread_only:
            rows = [n for n in rows if not n["is_read"]]
        return [dict(n) for n in rows]

    async def mark_read(self, user_id: str, notification_id: str) -> bool:
        row = self.rows.get(notification_id)
        if not row or row["user_id"] != user_id or row["is_read"]:
            return False
        row["is_read"] = True
        return True


@pytest_asyncio.fixture
async def client_and_repo() -> AsyncGenerator[tuple[AsyncClient, FakeNotificationRepository], None]:
    repo = FakeNotificationRepository()
    repo.seed("notif-1")
    repo.seed("notif-2", is_read=True)

    async def override_notification_service() -> NotificationService:
        return NotificationService(notification_repo=repo)

    app.dependency_overrides[get_notification_service] = override_notification_service
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, repo
    app.dependency_overrides.clear()


def auth(user_id: str = USER_ID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


@pytest.mark.asyncio
async def test_list_returns_notifications_with_unread_count(
    client_and_repo: tuple[AsyncClient, FakeNotificationRepository],
) -> None:
    client, _ = client_and_repo
    response = await client.get("/api/v1/notifications", headers=auth())

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data["notifications"]) == 2
    assert data["unread_count"] == 1


@pytest.mark.asyncio
async def test_unread_filter(client_and_repo: tuple[AsyncClient, FakeNotificationRepository]) -> None:
    client, _ = client_and_repo
    response = await client.get("/api/v1/notifications?unread_only=true", headers=auth())

    ids = [n["id"] for n in response.json()["data"]["notifications"]]
    assert ids == ["notif-1"]


@pytest.mark.asyncio
async def test_mark_read_is_owner_scoped_and_idempotent_404(
    client_and_repo: tuple[AsyncClient, FakeNotificationRepository],
) -> None:
    client, _ = client_and_repo
    foreign = await client.post("/api/v1/notifications/notif-1/read", headers=auth(OTHER_ID))
    assert foreign.status_code == 404

    first = await client.post("/api/v1/notifications/notif-1/read", headers=auth())
    second = await client.post("/api/v1/notifications/notif-1/read", headers=auth())

    assert first.status_code == 200
    assert first.json()["data"]["is_read"] is True
    assert second.status_code == 404  # already read -> nothing to mark


@pytest.mark.asyncio
async def test_module_is_read_only_by_design() -> None:
    """Invariant 12: no create/write endpoint exists on the notifications API."""
    from app.api.v1 import notifications as module

    methods = {
        (getattr(route, "path", ""), method)
        for route in module.router.routes
        for method in getattr(route, "methods", set())
    }
    assert all(method == "GET" or path.endswith("/read") for path, method in methods)
