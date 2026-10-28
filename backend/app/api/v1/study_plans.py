"""Study plan endpoints (US 2.16 + M5-06): request generation, list, item status."""

from collections.abc import AsyncGenerator
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.database import async_session_factory
from app.services.study_plan_service import StudyPlanService
from app.services.user_service import get_current_user_id

router = APIRouter(prefix="/study-plans", tags=["study-plans"])


async def get_study_plan_service(
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> AsyncGenerator[StudyPlanService, None]:
    """Request-scoped StudyPlanService."""
    async with async_session_factory() as session:
        yield StudyPlanService.from_session(session)


class PlanRequestRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    start_date: date
    end_date: date


class ItemStatusRequest(BaseModel):
    status: str = Field(pattern="^(pending|accepted|in_progress|completed|dismissed)$")


class PlanResponse(BaseModel):
    success: bool = True
    data: dict[str, Any]


class PlanListResponse(BaseModel):
    success: bool = True
    data: list[dict[str, Any]]


def _plain(row: dict[str, Any]) -> dict[str, Any]:
    return {k: (str(v) if v is not None else None) for k, v in row.items()}


@router.post("", response_model=PlanResponse, status_code=status.HTTP_202_ACCEPTED)
async def request_plan(
    payload: PlanRequestRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[StudyPlanService, Depends(get_study_plan_service)],
) -> PlanResponse:
    if payload.end_date < payload.start_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="end_date must be on or after start_date.",
        )
    job = await service.request_plan(
        user_id,
        name=payload.name,
        start_date=payload.start_date.isoformat(),
        end_date=payload.end_date.isoformat(),
    )
    return PlanResponse(data=_plain(job))


@router.get("", response_model=PlanListResponse)
async def list_plans(
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[StudyPlanService, Depends(get_study_plan_service)],
) -> PlanListResponse:
    plans = await service.list_plans(user_id)
    return PlanListResponse(data=[_plain(p) for p in plans])


@router.get("/{plan_id}", response_model=PlanResponse)
async def get_plan(
    plan_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[StudyPlanService, Depends(get_study_plan_service)],
) -> PlanResponse:
    try:
        detail = await service.get_plan_items(user_id, plan_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PlanResponse(
        data={
            "plan": _plain(detail["plan"]),
            "items": [_plain(i) for i in detail["items"]],
        }
    )


@router.patch("/items/{item_id}/status", response_model=PlanResponse)
async def update_item_status(
    item_id: str,
    payload: ItemStatusRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[StudyPlanService, Depends(get_study_plan_service)],
) -> PlanResponse:
    """M5-06: accept, start, complete, or dismiss a plan item."""
    try:
        item = await service.set_item_status(user_id, item_id, payload.status)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return PlanResponse(data=_plain(item))
