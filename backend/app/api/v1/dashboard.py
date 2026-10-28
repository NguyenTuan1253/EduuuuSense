"""Dashboard endpoints (US 2.17/2.18)."""

from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.database import async_session_factory
from app.services.progress_service import ProgressService
from app.services.user_service import get_current_user_id

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


async def get_progress_service(
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> AsyncGenerator[ProgressService, None]:
    """Request-scoped ProgressService."""
    async with async_session_factory() as session:
        yield ProgressService.from_session(session)


class DashboardResponse(BaseModel):
    success: bool = True
    data: dict[str, Any]


@router.get("/overview", response_model=DashboardResponse)
async def get_overview(
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[ProgressService, Depends(get_progress_service)],
) -> DashboardResponse:
    return DashboardResponse(data=await service.get_overview(user_id))


@router.get("/progress", response_model=DashboardResponse)
async def get_course_progress(
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[ProgressService, Depends(get_progress_service)],
) -> DashboardResponse:
    rows = await service.refresh_and_list_course_progress(user_id)
    return DashboardResponse(
        data={
            "courses": [
                {k: (str(v) if v is not None and not isinstance(v, (int, float)) else v) for k, v in row.items()}
                for row in rows
            ]
        }
    )
