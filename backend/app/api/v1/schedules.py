"""Weekly recurring class schedule endpoints (US 2.9) with overlap warnings."""

from collections.abc import AsyncGenerator
from datetime import date, time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator

from app.core.database import async_session_factory
from app.services.schedule_service import ScheduleNotFoundError, ScheduleService
from app.services.user_service import get_current_user_id

router = APIRouter(tags=["schedules"])


async def get_schedule_service(
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> AsyncGenerator[ScheduleService, None]:
    """Request-scoped ScheduleService composed over one database session."""
    async with async_session_factory() as session:
        yield ScheduleService.from_session(session)


class ScheduleCreateRequest(BaseModel):
    day_of_week: int = Field(ge=0, le=6, description="0=Sunday .. 6=Saturday (schema CHECK)")
    start_time: time
    end_time: time
    location: str | None = Field(default=None, max_length=255)
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def check_time_and_date_order(self) -> "ScheduleCreateRequest":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time (schema CHECK).")
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date (schema CHECK).")
        return self


class ScheduleUpdateRequest(BaseModel):
    day_of_week: int | None = Field(default=None, ge=0, le=6)
    start_time: time | None = None
    end_time: time | None = None
    location: str | None = Field(default=None, max_length=255)
    start_date: date | None = None
    end_date: date | None = None


class ScheduleData(BaseModel):
    id: str
    course_id: str
    day_of_week: int
    start_time: str
    end_time: str
    location: str | None = None
    start_date: str
    end_date: str
    created_at: str | None = None
    updated_at: str | None = None


class ScheduleResponse(BaseModel):
    success: bool = True
    data: ScheduleData
    warnings: list[dict[str, Any]] = []


class ScheduleListResponse(BaseModel):
    success: bool = True
    data: list[ScheduleData]


class ScheduleDeleteResponse(BaseModel):
    success: bool = True
    data: dict[str, Any]


def _to_data(row: dict[str, Any]) -> ScheduleData:
    return ScheduleData(
        id=str(row["id"]),
        course_id=str(row["course_id"]),
        day_of_week=int(row["day_of_week"]),
        start_time=str(row["start_time"]),
        end_time=str(row["end_time"]),
        location=row.get("location"),
        start_date=str(row["start_date"]),
        end_date=str(row["end_date"]),
        created_at=str(row["created_at"]) if row.get("created_at") is not None else None,
        updated_at=str(row["updated_at"]) if row.get("updated_at") is not None else None,
    )


def _not_found(exc: ScheduleNotFoundError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post(
    "/courses/{course_id}/schedules",
    response_model=ScheduleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_schedule(
    course_id: str,
    payload: ScheduleCreateRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[ScheduleService, Depends(get_schedule_service)],
) -> ScheduleResponse:
    try:
        schedule, warnings = await service.create_schedule(
            user_id, course_id, payload.model_dump(mode="json")
        )
    except ScheduleNotFoundError as exc:
        raise _not_found(exc) from exc
    return ScheduleResponse(data=_to_data(schedule), warnings=warnings)


@router.get("/courses/{course_id}/schedules", response_model=ScheduleListResponse)
async def list_schedules(
    course_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[ScheduleService, Depends(get_schedule_service)],
) -> ScheduleListResponse:
    rows = await service.list_schedules(user_id, course_id)
    return ScheduleListResponse(data=[_to_data(r) for r in rows])


@router.get("/schedules/{schedule_id}", response_model=ScheduleResponse)
async def get_schedule(
    schedule_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[ScheduleService, Depends(get_schedule_service)],
) -> ScheduleResponse:
    try:
        schedule = await service.get_schedule(user_id, schedule_id)
    except ScheduleNotFoundError as exc:
        raise _not_found(exc) from exc
    return ScheduleResponse(data=_to_data(schedule), warnings=[])


@router.patch("/schedules/{schedule_id}", response_model=ScheduleResponse)
async def update_schedule(
    schedule_id: str,
    payload: ScheduleUpdateRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[ScheduleService, Depends(get_schedule_service)],
) -> ScheduleResponse:
    changes = payload.model_dump(mode="json", exclude_unset=True)
    try:
        schedule, warnings = await service.update_schedule(
            user_id, schedule_id, changes
        )
    except ScheduleNotFoundError as exc:
        raise _not_found(exc) from exc
    return ScheduleResponse(data=_to_data(schedule), warnings=warnings)


@router.delete("/schedules/{schedule_id}", response_model=ScheduleDeleteResponse)
async def delete_schedule(
    schedule_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[ScheduleService, Depends(get_schedule_service)],
) -> ScheduleDeleteResponse:
    try:
        result = await service.delete_schedule(user_id, schedule_id)
    except ScheduleNotFoundError as exc:
        raise _not_found(exc) from exc
    return ScheduleDeleteResponse(data=result)
