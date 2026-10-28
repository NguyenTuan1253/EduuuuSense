"""AI chat endpoints (US 2.12). Owner-scoped; responses labeled AI-generated."""

from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.core.database import async_session_factory
from app.services.chat_service import ChatProviderError, ChatService, ConversationNotFoundError
from app.services.quota_service import QuotaExhaustedError, QuotaUnavailableError
from app.services.user_service import get_current_user_id

router = APIRouter(prefix="/chat", tags=["chat"])


async def get_chat_service(
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> AsyncGenerator[ChatService, None]:
    """Request-scoped ChatService wired with the caller's plan for quotas."""
    from app.repositories.user_repo import UserRepository

    async with async_session_factory() as session:
        user = await UserRepository(session).get_user_with_profile(user_id)
        plan_code = str(user["current_plan_code"]) if user else "free"
        yield ChatService.from_session(session, plan_code=plan_code)


class ConversationCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=255)


class MessageSendRequest(BaseModel):
    content: str = Field(min_length=1, max_length=8000)

    @field_validator("content")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank.")
        return value.strip()


class ChatResponse(BaseModel):
    success: bool = True
    data: dict[str, Any]


@router.post("/conversations", response_model=ChatResponse, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: ConversationCreateRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> ChatResponse:
    conversation = await service.create_conversation(user_id, title=payload.title)
    return ChatResponse(data={"conversation": _plain(conversation)})


@router.get("/conversations", response_model=ChatResponse)
async def list_conversations(
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> ChatResponse:
    conversations = await service.list_conversations(user_id)
    return ChatResponse(data={"conversations": [_plain(c) for c in conversations]})


@router.get("/conversations/{conversation_id}/messages", response_model=ChatResponse)
async def get_messages(
    conversation_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> ChatResponse:
    try:
        messages = await service.list_messages(user_id, conversation_id)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ChatResponse(data={"messages": [_plain(m) for m in messages], "ai_generated": True})


@router.post("/conversations/{conversation_id}/messages", response_model=ChatResponse)
async def send_message(
    conversation_id: str,
    payload: MessageSendRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> ChatResponse:
    try:
        result = await service.send_message(user_id, conversation_id, payload.content)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except QuotaExhaustedError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
        ) from exc
    except (QuotaUnavailableError, ChatProviderError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return ChatResponse(data=result)


def _plain(row: dict[str, Any]) -> dict[str, Any]:
    return {key: (str(value) if value is not None else None) for key, value in row.items()}
