"""Practice endpoints (US 2.15): start attempts, submit, view graded results."""

from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.database import async_session_factory
from app.services.grading_service import GradingOutputError
from app.services.practice_service import (
    AttemptAlreadySubmittedError,
    AttemptNotFoundError,
    PracticeService,
)
from app.services.user_service import get_current_user_id

router = APIRouter(prefix="/practice", tags=["practice"])


async def get_practice_service(
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> AsyncGenerator[PracticeService, None]:
    """Request-scoped PracticeService (grader composed in the service layer)."""
    async with async_session_factory() as session:
        yield PracticeService.from_session(session)


class AttemptStartRequest(BaseModel):
    question_bank_id: str


class AnswerItem(BaseModel):
    question_id: str
    selected_option: str | None = Field(default=None, max_length=200)
    answer_text: str | None = Field(default=None, max_length=8000)


class AttemptSubmitRequest(BaseModel):
    answers: list[AnswerItem] = Field(min_length=1, max_length=50)


class AttemptResponse(BaseModel):
    success: bool = True
    data: dict[str, Any]


@router.post("/attempts", response_model=AttemptResponse, status_code=status.HTTP_201_CREATED)
async def start_attempt(
    payload: AttemptStartRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[PracticeService, Depends(get_practice_service)],
) -> AttemptResponse:
    try:
        attempt = await service.start_attempt(user_id, payload.question_bank_id)
    except AttemptNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return AttemptResponse(data={k: (str(v) if v is not None else None) for k, v in attempt.items()})


@router.post("/attempts/{attempt_id}/submit", response_model=AttemptResponse)
async def submit_attempt(
    attempt_id: str,
    payload: AttemptSubmitRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[PracticeService, Depends(get_practice_service)],
) -> AttemptResponse:
    try:
        result = await service.submit_attempt(
            user_id, attempt_id, [answer.model_dump() for answer in payload.answers]
        )
    except AttemptNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AttemptAlreadySubmittedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (ValueError, GradingOutputError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return AttemptResponse(data={k: (str(v) if v is not None else None) for k, v in result.items()})


@router.get("/attempts/{attempt_id}", response_model=AttemptResponse)
async def get_attempt(
    attempt_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[PracticeService, Depends(get_practice_service)],
) -> AttemptResponse:
    try:
        detail = await service.get_attempt_detail(user_id, attempt_id)
    except AttemptNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return AttemptResponse(
        data={
            "attempt": {k: (str(v) if v is not None else None) for k, v in detail["attempt"].items()},
            "results": [
                {k: (str(v) if v is not None and not isinstance(v, (list, dict)) else v) for k, v in r.items()}
                for r in detail["results"]
            ],
        }
    )
