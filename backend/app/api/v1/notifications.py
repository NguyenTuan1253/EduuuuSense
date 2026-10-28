"""Notification endpoints (US 2.20): read-only + mark-read (Invariant 12)."""

from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.core.database import async_session_factory
from app.services.notification_service import NotificationNotFoundError, NotificationService
from app.services.user_service import get_current_user_id

router = APIRouter(prefix="/notifications", tags=["notifications"])


async def get_notification_service(
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> AsyncGenerator[NotificationService, None]:
    """Request-scoped NotificationService (read-side only)."""
    async with async_session_factory() as session:
        yield NotificationService.from_session(session)


class NotificationListResponse(BaseModel):
    success: bool = True
    data: dict[str, Any]


class MarkReadResponse(BaseModel):
    success: bool = True
    data: dict[str, Any]


def _plain(row: dict[str, Any]) -> dict[str, Any]:
    return {k: (str(v) if v is not None and not isinstance(v, bool) else v) for k, v in row.items()}


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[NotificationService, Depends(get_notification_service)],
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=100),
) -> NotificationListResponse:
    result = await service.list_notifications(user_id, unread_only=unread_only, limit=limit)
    return NotificationListResponse(
        data={
            "notifications": [_plain(n) for n in result["notifications"]],
            "unread_count": result["unread_count"],
        }
    )


@router.post("/{notification_id}/read", response_model=MarkReadResponse)
async def mark_read(
    notification_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[NotificationService, Depends(get_notification_service)],
) -> MarkReadResponse:
    """Marks read; idempotent-safe (already-read -> 404 by design, no error leak)."""
    try:
        result = await service.mark_read(user_id, notification_id)
    except NotificationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return MarkReadResponse(data=result)
