"""AI job endpoints (US 2.14+): request summarization, poll job status."""

from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.core.database import async_session_factory
from app.services.summary_service import (
    DocumentNotFoundError,
    DocumentValidationError,
    SummaryService,
)
from app.services.user_service import get_current_user_id

router = APIRouter(prefix="/ai", tags=["ai"])


async def get_summary_service(
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> AsyncGenerator[SummaryService, None]:
    """Request-scoped SummaryService; enqueue is the real Celery task."""
    async with async_session_factory() as session:
        yield SummaryService.from_session(session)


class JobResponse(BaseModel):
    success: bool = True
    data: dict[str, Any]


@router.post("/documents/{document_id}/summary", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def request_summary(
    document_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[SummaryService, Depends(get_summary_service)],
) -> JobResponse:
    """202 with a job_id; the summary is produced by the worker (never in-request)."""
    try:
        job = await service.request_summary(user_id, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DocumentValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return JobResponse(data={**{k: (str(v) if v is not None else None) for k, v in job.items()}})


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[SummaryService, Depends(get_summary_service)],
) -> JobResponse:
    """Owner-scoped job polling: status, progress, and the persisted result."""
    try:
        job = await service.get_job(user_id, job_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return JobResponse(data={k: (str(v) if v is not None else None) for k, v in job.items()})
