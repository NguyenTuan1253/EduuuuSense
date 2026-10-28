"""Question bank endpoints (US 2.15)."""

from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.database import async_session_factory
from app.services.question_service import QuestionBankService
from app.services.user_service import get_current_user_id

router = APIRouter(tags=["questions"])


async def get_question_service(
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> AsyncGenerator[QuestionBankService, None]:
    """Request-scoped QuestionBankService."""
    async with async_session_factory() as session:
        yield QuestionBankService.from_session(session)


class GenerationRequest(BaseModel):
    count: int = Field(default=5, ge=1, le=20)
    question_type: str = Field(default="multiple_choice", pattern="^(multiple_choice|essay)$")


class JobResponse(BaseModel):
    success: bool = True
    data: dict[str, Any]


class BankListResponse(BaseModel):
    success: bool = True
    data: list[dict[str, Any]]


class QuestionListResponse(BaseModel):
    success: bool = True
    data: list[dict[str, Any]]


@router.post("/ai/documents/{document_id}/questions", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def request_question_generation(
    document_id: str,
    payload: GenerationRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[QuestionBankService, Depends(get_question_service)],
) -> JobResponse:
    try:
        job = await service.request_generation(
            user_id, document_id, count=payload.count, question_type=payload.question_type
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return JobResponse(data={k: (str(v) if v is not None else None) for k, v in job.items()})


@router.get("/ai/question-banks", response_model=BankListResponse)
async def list_question_banks(
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[QuestionBankService, Depends(get_question_service)],
) -> BankListResponse:
    banks = await service.list_banks(user_id)
    return BankListResponse(data=[{k: (str(v) if v is not None else None) for k, v in b.items()} for b in banks])


@router.get("/ai/question-banks/{bank_id}/questions", response_model=QuestionListResponse)
async def list_bank_questions(
    bank_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[QuestionBankService, Depends(get_question_service)],
) -> QuestionListResponse:
    questions = await service.get_bank_questions(user_id, bank_id)
    if not questions:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question bank not found.")
    return QuestionListResponse(
        data=[{k: (str(v) if v is not None and not isinstance(v, (list, dict)) else v) for k, v in q.items()} for q in questions]
    )
