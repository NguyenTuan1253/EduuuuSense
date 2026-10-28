"""Course endpoints (US: Manage courses). Owner-scoped, thin router."""

from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator

from app.core.database import async_session_factory
from app.services.course_service import (
    CourseNotFoundError,
    CourseService,
    DuplicateCourseError,
)
from app.services.user_service import get_current_user_id

router = APIRouter(prefix="/courses", tags=["courses"])


async def get_course_service(
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> AsyncGenerator[CourseService, None]:
    """Request-scoped CourseService composed over one database session."""
    async with async_session_factory() as session:
        yield CourseService.from_session(session)


class CourseCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    course_code: str | None = Field(default=None, max_length=50)
    instructor: str | None = Field(default=None, max_length=255)
    semester: str | None = Field(default=None, max_length=50)
    academic_year: str | None = Field(default=None, max_length=20)

    @field_validator("name")
    @classmethod
    def reject_blank_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank.")
        return stripped


class CourseUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    course_code: str | None = Field(default=None, max_length=50)
    instructor: str | None = Field(default=None, max_length=255)
    semester: str | None = Field(default=None, max_length=50)
    academic_year: str | None = Field(default=None, max_length=20)
    status: str | None = Field(default=None, pattern="^(active|archived)$")

    @field_validator("name")
    @classmethod
    def reject_blank_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank.")
        return stripped


class CourseData(BaseModel):
    id: str
    course_code: str | None = None
    name: str
    description: str | None = None
    instructor: str | None = None
    semester: str | None = None
    academic_year: str | None = None
    status: str
    created_at: str | None = None
    updated_at: str | None = None


class CourseResponse(BaseModel):
    success: bool = True
    data: CourseData


class CourseListResponse(BaseModel):
    success: bool = True
    data: list[CourseData]


class CourseDeleteResponse(BaseModel):
    success: bool = True
    data: dict[str, Any]


def _to_data(course: dict[str, Any]) -> CourseData:
    return CourseData(
        id=str(course["id"]),
        course_code=course.get("course_code"),
        name=course["name"],
        description=course.get("description"),
        instructor=course.get("instructor"),
        semester=course.get("semester"),
        academic_year=course.get("academic_year"),
        status=course["status"],
        created_at=str(course["created_at"]) if course.get("created_at") else None,
        updated_at=str(course["updated_at"]) if course.get("updated_at") else None,
    )


@router.post("", response_model=CourseResponse, status_code=status.HTTP_201_CREATED)
async def create_course(
    payload: CourseCreateRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[CourseService, Depends(get_course_service)],
) -> CourseResponse:
    data = payload.model_dump()
    try:
        course = await service.create_course(user_id, data)
    except DuplicateCourseError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return CourseResponse(data=_to_data(course))


@router.get("", response_model=CourseListResponse)
async def list_courses(
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[CourseService, Depends(get_course_service)],
    course_status: str = Query(
        default="active",
        alias="status",
        pattern="^(active|archived|all)$",
        description="Default 'active': archived courses are hidden unless requested.",
    ),
) -> CourseListResponse:
    courses = await service.list_courses(
        user_id, None if course_status == "all" else course_status
    )
    return CourseListResponse(data=[_to_data(c) for c in courses])


@router.get("/{course_id}", response_model=CourseResponse)
async def get_course(
    course_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[CourseService, Depends(get_course_service)],
) -> CourseResponse:
    try:
        course = await service.get_course(user_id, course_id)
    except CourseNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return CourseResponse(data=_to_data(course))


@router.patch("/{course_id}", response_model=CourseResponse)
async def update_course(
    course_id: str,
    payload: CourseUpdateRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[CourseService, Depends(get_course_service)],
) -> CourseResponse:
    changes = {key: getattr(payload, key) for key in payload.model_fields_set}
    try:
        course = await service.update_course(user_id, course_id, changes)
    except CourseNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DuplicateCourseError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return CourseResponse(data=_to_data(course))


@router.delete("/{course_id}", response_model=CourseDeleteResponse)
async def delete_course(
    course_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[CourseService, Depends(get_course_service)],
) -> CourseDeleteResponse:
    """Delete requires confirmation on the client; the API reports exactly what
    the cascade removed (assignments, exams, schedules, documents)."""
    try:
        result = await service.delete_course(user_id, course_id)
    except CourseNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return CourseDeleteResponse(data=result)
