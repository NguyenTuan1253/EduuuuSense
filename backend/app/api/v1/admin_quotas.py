"""Admin AI usage analytics & quota policy endpoints (US 2.23)."""

from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.database import async_session_factory
from app.services.admin_quota_service import AdminQuotaService, PolicyNotFoundError
from app.services.user_service import require_admin

router = APIRouter(prefix="/admin/ai", tags=["admin"])


async def get_admin_quota_service(
    admin_id: Annotated[str, Depends(require_admin)],
) -> AsyncGenerator[AdminQuotaService, None]:
    """Admin-gated quota service."""
    async with async_session_factory() as session:
        yield AdminQuotaService.from_session(session)


class AdminListResponse(BaseModel):
    success: bool = True
    data: list[dict[str, Any]]


class PolicyResponse(BaseModel):
    success: bool = True
    data: dict[str, Any]


class PolicyUpdateRequest(BaseModel):
    request_limit_per_day: int = Field(ge=0)
    token_limit_per_day: int | None = Field(default=None, ge=0)
    warning_threshold_percent: int = Field(ge=1, le=100)


@router.get("/usage", response_model=AdminListResponse)
async def usage_by_day(
    admin_id: Annotated[str, Depends(require_admin)],
    service: Annotated[AdminQuotaService, Depends(get_admin_quota_service)],
    day: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    limit: int = Query(default=50, ge=1, le=100),
) -> AdminListResponse:
    rows = await service.usage_by_day(day=day, limit=limit)
    return AdminListResponse(data=[{k: (str(v) if v is not None else None) for k, v in r.items()} for r in rows])


@router.get("/usage/features", response_model=AdminListResponse)
async def usage_by_feature(
    admin_id: Annotated[str, Depends(require_admin)],
    service: Annotated[AdminQuotaService, Depends(get_admin_quota_service)],
    day: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
) -> AdminListResponse:
    rows = await service.usage_by_feature(day=day)
    return AdminListResponse(data=[{k: (str(v) if v is not None else None) for k, v in r.items()} for r in rows])


@router.get("/quota-policies/{plan_code}", response_model=PolicyResponse)
async def get_policy(
    plan_code: str,
    admin_id: Annotated[str, Depends(require_admin)],
    service: Annotated[AdminQuotaService, Depends(get_admin_quota_service)],
) -> PolicyResponse:
    try:
        policy = await service.get_policy(plan_code)
    except PolicyNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PolicyResponse(data={k: (str(v) if v is not None else None) for k, v in policy.items()})


@router.put("/quota-policies/{plan_code}", response_model=PolicyResponse)
async def update_policy(
    plan_code: str,
    payload: PolicyUpdateRequest,
    admin_id: Annotated[str, Depends(require_admin)],
    service: Annotated[AdminQuotaService, Depends(get_admin_quota_service)],
) -> PolicyResponse:
    """Admin configures plan quotas (updated_by audit trail)."""
    try:
        policy = await service.update_policy(
            admin_id,
            plan_code,
            request_limit_per_day=payload.request_limit_per_day,
            token_limit_per_day=payload.token_limit_per_day,
            warning_threshold_percent=payload.warning_threshold_percent,
        )
    except PolicyNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return PolicyResponse(data={k: (str(v) if v is not None else None) for k, v in policy.items()})
