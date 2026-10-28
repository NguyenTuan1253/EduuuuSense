"""Admin system report endpoints (US 2.24)."""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.core.database import async_session_factory
from app.services.system_report_service import SystemReportService
from app.services.user_service import require_admin

router = APIRouter(prefix="/admin/system", tags=["admin"])


async def get_system_report_service(
    admin_id: Annotated[str, Depends(require_admin)],
) -> AsyncGenerator[SystemReportService, None]:
    """Admin-gated system report service."""
    async with async_session_factory() as session:
        yield SystemReportService.from_session(session)


class ReportResponse(BaseModel):
    success: bool = True
    data: dict


@router.get("/storage", response_model=ReportResponse)
async def storage_report(
    admin_id: Annotated[str, Depends(require_admin)],
    service: Annotated[SystemReportService, Depends(get_system_report_service)],
    limit: int = Query(default=100, ge=1, le=200),
) -> ReportResponse:
    """US 2.24: storage consumption totals + largest consumers."""
    return ReportResponse(data=await service.storage_report(limit=limit))


@router.get("/resources", response_model=ReportResponse)
async def resource_report(
    admin_id: Annotated[str, Depends(require_admin)],
    service: Annotated[SystemReportService, Depends(get_system_report_service)],
) -> ReportResponse:
    """US 2.24: system-wide record and job counts."""
    return ReportResponse(data=await service.resource_report())
