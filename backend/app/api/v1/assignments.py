"""Assignment endpoints (US 2.7). Nested under courses; owner-scoped."""

from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator

from app.core.database import async_session_factory
from app.services.assignment_service import AssignmentNotFoundError, AssignmentService
from app.services.user_service import get_current_user_id

router = APIRouter(tags=["assignments"])


async def get_assignment_service(
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> AsyncGenerator[AssignmentService, None]:
    """Request-scoped AssignmentService composed over one database session."""
    async with async_session_factory() as session:
        yield AssignmentService.from_session(session)


class AssignmentCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    due_at: str | None = Field(default=None, max_length=40)
    priority: str = Field(default="medium", pattern="^(low|medium|high)$")

    @field_validator("title")
    @classmethod
    def reject_blank_title(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("title must not be blank.")
        return stripped


class AssignmentUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    due_at: str | None = Field(default=None, max_length=40)
    priority: str | None = Field(default=None, pattern="^(low|medium|high)$")
    status: str | None = Field(default=None, pattern="^(pending|in_progress|completed)$")

    @field_validator("title")
    @classmethod
    def reject_blank_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("title must not be blank.")
        return stripped


class AssignmentData(BaseModel):
    id: str
    course_id: str
    title: str
    description: str | None = None
    due_at: str | None = None
    priority: str
    status: str
    completed_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class AssignmentResponse(BaseModel):
    success: bool = True
    data: AssignmentData


class AssignmentListResponse(BaseModel):
    success: bool = True
    data: list[AssignmentData]


class AssignmentDeleteResponse(BaseModel):
    success: bool = True
    data: dict[str, Any]


def _to_data(row: dict[str, Any]) -> AssignmentData:
    return AssignmentData(
        id=str(row["id"]),
        course_id=str(row["course_id"]),
        title=row["title"],
        description=row.get("description"),
        due_at=str(row["due_at"]) if row.get("due_at") is not None else None,
        priority=row["priority"],
        status=row["status"],
        completed_at=str(row["completed_at"]) if row.get("completed_at") is not None else None,
        created_at=str(row["created_at"]) if row.get("created_at") is not None else None,
        updated_at=str(row["updated_at"]) if row.get("updated_at") is not None else None,
    )


def _not_found(exc: AssignmentNotFoundError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post(
    "/courses/{course_id}/assignments",
    response_model=AssignmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_assignment(
    course_id: str,
    payload: AssignmentCreateRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[AssignmentService, Depends(get_assignment_service)],
) -> AssignmentResponse:
    try:
        row = await service.create_assignment(user_id, course_id, payload.model_dump())
    except AssignmentNotFoundError as exc:
        raise _not_found(exc) from exc
    return AssignmentResponse(data=_to_data(row))


@router.get("/courses/{course_id}/assignments", response_model=AssignmentListResponse)
async def list_assignments(
    course_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[AssignmentService, Depends(get_assignment_service)],
    assignment_status: str | None = Query(
        default=None, alias="status", pattern="^(pending|in_progress|completed)$"
    ),
) -> AssignmentListResponse:
    rows = await service.list_assignments(user_id, course_id, assignment_status)
    return AssignmentListResponse(data=[_to_data(r) for r in rows])


@router.get("/assignments/{assignment_id}", response_model=AssignmentResponse)
async def get_assignment(
    assignment_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[AssignmentService, Depends(get_assignment_service)],
) -> AssignmentResponse:
    try:
        row = await service.get_assignment(user_id, assignment_id)
    except AssignmentNotFoundError as exc:
        raise _not_found(exc) from exc
    return AssignmentResponse(data=_to_data(row))


@router.patch("/assignments/{assignment_id}", response_model=AssignmentResponse)
async def update_assignment(
    assignment_id: str,
    payload: AssignmentUpdateRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[AssignmentService, Depends(get_assignment_service)],
) -> AssignmentResponse:
    changes = {key: getattr(payload, key) for key in payload.model_fields_set}
    try:
        row = await service.update_assignment(user_id, assignment_id, changes)
    except AssignmentNotFoundError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return AssignmentResponse(data=_to_data(row))


@router.delete("/assignments/{assignment_id}", response_model=AssignmentDeleteResponse)
async def delete_assignment(
    assignment_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[AssignmentService, Depends(get_assignment_service)],
) -> AssignmentDeleteResponse:
    try:
        result = await service.delete_assignment(user_id, assignment_id)
    except AssignmentNotFoundError as exc:
        raise _not_found(exc) from exc
    return AssignmentDeleteResponse(data=result)
