"""Exam endpoints (US 2.8). Nested under courses; owner-scoped."""

from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.core.database import async_session_factory
from app.services.exam_service import ExamNotFoundError, ExamService
from app.services.user_service import get_current_user_id

router = APIRouter(tags=["exams"])


async def get_exam_service(
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> AsyncGenerator[ExamService, None]:
    """Request-scoped ExamService composed over one database session."""
    async with async_session_factory() as session:
        yield ExamService.from_session(session)


class ExamCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    exam_at: str = Field(min_length=10, max_length=40)
    duration_minutes: int | None = Field(default=None, ge=1, le=24 * 60)
    location: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("title")
    @classmethod
    def reject_blank_title(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("title must not be blank.")
        return stripped


class ExamUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    exam_at: str | None = Field(default=None, min_length=10, max_length=40)
    duration_minutes: int | None = Field(default=None, ge=1, le=24 * 60)
    location: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("title")
    @classmethod
    def reject_blank_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("title must not be blank.")
        return stripped


class ExamData(BaseModel):
    id: str
    course_id: str
    title: str
    exam_at: str
    duration_minutes: int | None = None
    location: str | None = None
    notes: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ExamResponse(BaseModel):
    success: bool = True
    data: ExamData


class ExamListResponse(BaseModel):
    success: bool = True
    data: list[ExamData]


class ExamDeleteResponse(BaseModel):
    success: bool = True
    data: dict[str, Any]


def _to_data(row: dict[str, Any]) -> ExamData:
    exam_at = row.get("exam_at")
    return ExamData(
        id=str(row["id"]),
        course_id=str(row["course_id"]),
        title=row["title"],
        exam_at=str(exam_at) if exam_at is not None else "",
        duration_minutes=row.get("duration_minutes"),
        location=row.get("location"),
        notes=row.get("notes"),
        created_at=str(row["created_at"]) if row.get("created_at") is not None else None,
        updated_at=str(row["updated_at"]) if row.get("updated_at") is not None else None,
    )


def _not_found(exc: ExamNotFoundError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post(
    "/courses/{course_id}/exams",
    response_model=ExamResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_exam(
    course_id: str,
    payload: ExamCreateRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[ExamService, Depends(get_exam_service)],
) -> ExamResponse:
    try:
        row = await service.create_exam(user_id, course_id, payload.model_dump())
    except ExamNotFoundError as exc:
        raise _not_found(exc) from exc
    return ExamResponse(data=_to_data(row))


@router.get("/courses/{course_id}/exams", response_model=ExamListResponse)
async def list_exams(
    course_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[ExamService, Depends(get_exam_service)],
) -> ExamListResponse:
    rows = await service.list_exams(user_id, course_id)
    return ExamListResponse(data=[_to_data(r) for r in rows])


@router.get("/exams/{exam_id}", response_model=ExamResponse)
async def get_exam(
    exam_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[ExamService, Depends(get_exam_service)],
) -> ExamResponse:
    try:
        row = await service.get_exam(user_id, exam_id)
    except ExamNotFoundError as exc:
        raise _not_found(exc) from exc
    return ExamResponse(data=_to_data(row))


@router.patch("/exams/{exam_id}", response_model=ExamResponse)
async def update_exam(
    exam_id: str,
    payload: ExamUpdateRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[ExamService, Depends(get_exam_service)],
) -> ExamResponse:
    changes = {key: getattr(payload, key) for key in payload.model_fields_set}
    try:
        row = await service.update_exam(user_id, exam_id, changes)
    except ExamNotFoundError as exc:
        raise _not_found(exc) from exc
    return ExamResponse(data=_to_data(row))


@router.delete("/exams/{exam_id}", response_model=ExamDeleteResponse)
async def delete_exam(
    exam_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[ExamService, Depends(get_exam_service)],
) -> ExamDeleteResponse:
    try:
        result = await service.delete_exam(user_id, exam_id)
    except ExamNotFoundError as exc:
        raise _not_found(exc) from exc
    return ExamDeleteResponse(data=result)
