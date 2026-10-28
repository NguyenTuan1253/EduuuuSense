"""Authentication endpoints: email/password registration (US 2.1).

Routers stay thin: request/response schemas and dependency wiring only. All
business logic lives in the auth service; all SQL lives in the user repository.
"""

import re
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.core.database import async_session_factory
from app.services.auth_service import AuthService, DuplicateEmailError

router = APIRouter(prefix="/auth", tags=["auth"])

# Pragmatic RFC-5321-shaped pattern. The email-validator package is
# intentionally not added (no dependency additions without approval); this
# validation covers format, not deliverability.
_EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")


class RegisterRequest(BaseModel):
    """Registration input. Validation rules at the system boundary."""

    full_name: str = Field(min_length=1, max_length=150)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("full_name must not be blank.")
        return stripped

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _EMAIL_PATTERN.fullmatch(normalized):
            raise ValueError("email must be a valid email address.")
        return normalized

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, value: str) -> str:
        # Proposed default policy pending Product Owner approval (no document
        # defines "password strength"): minimum 8 characters with at least one
        # letter and one digit; maximum 128 to bound hashing cost.
        has_letter = any(ch.isalpha() for ch in value)
        has_digit = any(ch.isdigit() for ch in value)
        if not (has_letter and has_digit):
            raise ValueError("Password must contain at least one letter and one digit.")
        return value


class RegisteredUser(BaseModel):
    """Public representation of a newly registered user."""

    user_id: str
    email: str
    is_email_verified: bool


class RegisterResponse(BaseModel):
    """Success envelope for registration."""

    success: bool = True
    data: RegisteredUser


async def get_auth_service() -> AsyncGenerator[AuthService, None]:
    """Request-scoped AuthService composed over one database session."""
    async with async_session_factory() as session:
        yield AuthService.from_session(session)


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new student account with email and password (US 2.1)",
)
async def register(
    payload: RegisterRequest,
    service: AuthService = Depends(get_auth_service),
) -> RegisterResponse:
    try:
        user = await service.register_user(
            email=payload.email,
            password=payload.password,
            full_name=payload.full_name,
        )
    except DuplicateEmailError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        ) from exc

    return RegisterResponse(
        data=RegisteredUser(
            user_id=user["user_id"],
            email=user["email"],
            is_email_verified=user["is_email_verified"],
        )
    )
