"""Admin endpoints: student account management (US 2.22)."""

from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.core.database import async_session_factory
from app.services.user_service import AdminUserService, require_admin

router = APIRouter(prefix="/admin", tags=["admin"])


async def get_admin_user_service(
    admin_id: Annotated[str, Depends(require_admin)],
) -> AsyncGenerator[AdminUserService, None]:
    """Admin-gated AdminUserService."""
    async with async_session_factory() as session:
        yield AdminUserService.from_session(session)


class AccountData(BaseModel):
    id: str
    email: str
    full_name: str | None = None
    is_active: bool
    is_email_verified: bool
    current_plan_code: str
    last_login_at: str | None = None


class AccountListResponse(BaseModel):
    success: bool = True
    data: list[AccountData]


class AccountActionResponse(BaseModel):
    success: bool = True
    data: dict[str, Any]


class AccountUpdateRequest(BaseModel):
    is_active: bool


@router.get("/users", response_model=AccountListResponse)
async def list_accounts(
    admin_id: Annotated[str, Depends(require_admin)],
    service: Annotated[AdminUserService, Depends(get_admin_user_service)],
    search: str | None = Query(default=None, max_length=100),
    is_active: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> AccountListResponse:
    """US 2.22: view/search student accounts. Students get 403 (RBAC)."""
    rows = await service.list_accounts(search=search, is_active=is_active, limit=limit, offset=offset)
    return AccountListResponse(
        data=[
            AccountData(
                id=str(r["id"]),
                email=r["email"],
                full_name=r.get("full_name"),
                is_active=bool(r["is_active"]),
                is_email_verified=bool(r["is_email_verified"]),
                current_plan_code=str(r["current_plan_code"]),
                last_login_at=str(r["last_login_at"]) if r.get("last_login_at") else None,
            )
            for r in rows
        ]
    )


@router.patch("/users/{user_id}", response_model=AccountActionResponse)
async def update_account(
    user_id: str,
    payload: AccountUpdateRequest,
    admin_id: Annotated[str, Depends(require_admin)],
    service: Annotated[AdminUserService, Depends(get_admin_user_service)],
) -> AccountActionResponse:
    """Suspend/activate an account. Suspension revokes ALL refresh sessions."""
    try:
        result = await service.set_account_active(admin_id, user_id, payload.is_active)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return AccountActionResponse(data=result)
