"""Student profile endpoints (US 2.4). Owner-only via the JWT subject."""

from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.core.database import async_session_factory
from app.services.user_service import UserService, get_current_user_id

router = APIRouter(prefix="/users", tags=["users"])


async def get_user_service(
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> AsyncGenerator[UserService, None]:
    """Request-scoped UserService composed over one database session."""
    async with async_session_factory() as session:
        yield UserService.from_session(session)


class ProfileData(BaseModel):
    full_name: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None
    phone_number: str | None = None
    university: str | None = None
    major: str | None = None
    academic_year: int | None = None
    updated_at: str | None = None


class AccountData(BaseModel):
    user_id: str
    email: str
    is_email_verified: bool
    current_plan_code: str


class ProfileResponse(BaseModel):
    success: bool = True
    data: dict[str, Any]


class ProfileUpdateRequest(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=150)
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    avatar_url: str | None = Field(default=None, max_length=2048)
    phone_number: str | None = Field(default=None, max_length=20)
    university: str | None = Field(default=None, max_length=255)
    major: str | None = Field(default=None, max_length=255)
    academic_year: int | None = Field(default=None, ge=1, le=10)

    @field_validator("full_name", "display_name")
    @classmethod
    def reject_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("Value must not be blank.")
        return stripped


@router.get(
    "/me",
    response_model=ProfileResponse,
    summary="Get the authenticated user's account and profile (US 2.4)",
)
async def get_me(
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[UserService, Depends(get_user_service)],
) -> ProfileResponse:
    row = await service.get_own_profile(user_id)
    account = {
        "user_id": str(row["id"]),
        "email": row["email"],
        "is_email_verified": row["is_email_verified"],
        "current_plan_code": row["current_plan_code"],
    }
    profile = {
        key: row.get(key)
        for key in (
            "full_name",
            "display_name",
            "avatar_url",
            "phone_number",
            "university",
            "major",
            "academic_year",
            "profile_updated_at",
        )
    }
    return ProfileResponse(data={"account": account, "profile": profile})


@router.put(
    "/me/profile",
    response_model=ProfileResponse,
    summary="Update the authenticated user's profile (US 2.4)",
)
async def update_my_profile(
    payload: ProfileUpdateRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[UserService, Depends(get_user_service)],
) -> ProfileResponse:
    # Only explicitly provided fields are applied (PATCH-style PUT).
    changes = {key: getattr(payload, key) for key in payload.model_fields_set}
    if not changes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide at least one profile field to update.",
        )
    try:
        updated = await service.update_own_profile(user_id, changes)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return ProfileResponse(data={"profile": updated})
