"""Question bank service + endpoints (US 2.15): request generation, list banks/questions."""

from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.database import async_session_factory
from app.repositories.background_job_repo import BackgroundJobRepository
from app.repositories.document_repo import DocumentRepository
from app.repositories.question_repo import QuestionRepository
from app.services.user_service import get_current_user_id

router = APIRouter(tags=["questions"])


class QuestionBankService:
    """Question-bank orchestration for the authenticated owner."""

    def __init__(
        self,
        job_repo: BackgroundJobRepository,
        document_repo: DocumentRepository,
        question_repo: QuestionRepository,
        enqueue: Any | None = None,
    ) -> None:
        self._job_repo = job_repo
        self._document_repo = document_repo
        self._question_repo = question_repo
        self._enqueue = enqueue or self._default_enqueue

    @classmethod
    def from_session(cls, session: Any, enqueue: Any | None = None) -> "QuestionBankService":
        return cls(
            job_repo=BackgroundJobRepository(session),
            document_repo=DocumentRepository(session),
            question_repo=QuestionRepository(session),
            enqueue=enqueue,
        )

    @staticmethod
    def _default_enqueue(job_id: str, user_id: str, document_id: str, count: int, question_type: str, plan_code: str) -> None:
        from app.worker.tasks.questions import question_generation_job

        question_generation_job.delay(job_id, user_id, document_id, count, question_type, plan_code)

    async def request_generation(
        self,
        user_id: str,
        document_id: str,
        *,
        count: int,
        question_type: str,
        plan_code: str = "free",
    ) -> dict[str, Any]:
        document = await self._document_repo.get_document(user_id, document_id)
        if document is None:
            raise FileNotFoundError("Document not found.")
        if document["status"] != "embedded":
            raise ValueError(f"Document is not ready (status: {document['status']}).")
        job = await self._job_repo.enqueue(
            user_id=user_id,
            job_type="question_generation",
            idempotency_key=f"question_generation:{document_id}:{question_type}",
        )
        if job["status"] == "queued":
            self._enqueue(str(job["id"]), user_id, document_id, count, question_type, plan_code)
        return job

    async def list_banks(self, user_id: str) -> list[dict[str, Any]]:
        return await self._question_repo.list_banks(user_id)

    async def get_bank_questions(self, user_id: str, bank_id: str) -> list[dict[str, Any]]:
        return await self._question_repo.get_bank_questions(user_id, bank_id)


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


async def get_question_service(
    user_id: str = Depends(get_current_user_id),
) -> AsyncGenerator[QuestionBankService, None]:
    """Request-scoped QuestionBankService."""
    async with async_session_factory() as session:
        yield QuestionBankService.from_session(session)


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
