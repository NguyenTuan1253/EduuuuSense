"""Aggregated academic calendar endpoint (US 2.9)."""

from collections.abc import AsyncGenerator
from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.core.database import async_session_factory
from app.services.calendar_service import CalendarService, InvalidCalendarRangeError
from app.services.user_service import get_current_user_id

router = APIRouter(prefix="/calendar", tags=["calendar"])


class CalendarResponse(BaseModel):
    success: bool = True
    data: dict[str, Any]


async def get_calendar_service(
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> AsyncGenerator[CalendarService, None]:
    """Request-scoped CalendarService composed over one database session."""
    async with async_session_factory() as session:
        yield CalendarService.from_session(session)


@router.get("", response_model=CalendarResponse)
async def get_calendar(
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[CalendarService, Depends(get_calendar_service)],
    start: date | None = Query(default=None, description="Inclusive range start (ISO date)"),
    end: date | None = Query(default=None, description="Inclusive range end (ISO date)"),
) -> CalendarResponse:
    """Day/week/month views are range queries; items carry a type so the client
    can color/icon distinguish classes, exams, and deadlines (US 2.9)."""
    today = date.today()
    range_start = start or today
    range_end = end or today + timedelta(days=6)
    try:
        calendar = await service.get_calendar(user_id, range_start, range_end)
    except InvalidCalendarRangeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return CalendarResponse(data=calendar)
