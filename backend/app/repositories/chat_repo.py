"""Chat persistence: SQL for ai_conversations and ai_messages (US 2.12).

Ownership enforced in every statement (conversations belong to users).
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class ChatRepository:
    """SQL access for the chat aggregate. No business logic lives here."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_conversation(
        self, *, user_id: str, title: str | None, course_id: str | None, conversation_type: str = "general"
    ) -> dict[str, Any]:
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "INSERT INTO ai_conversations (user_id, course_id, title, conversation_type) "
                    "VALUES (:user_id, :course_id, :title, :conversation_type) "
                    "RETURNING id, user_id, course_id, title, conversation_type, "
                    "created_at, updated_at"
                ),
                {
                    "user_id": user_id,
                    "course_id": course_id,
                    "title": title,
                    "conversation_type": conversation_type,
                },
            )
            row = result.mappings().one()
        return dict(row)

    async def list_conversations(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT id, user_id, course_id, title, conversation_type, "
                    "created_at, updated_at FROM ai_conversations "
                    "WHERE user_id = :user_id ORDER BY updated_at DESC LIMIT :limit"
                ),
                {"user_id": user_id, "limit": limit},
            )
            rows = result.mappings().all()
        return [dict(row) for row in rows]

    async def get_conversation(self, user_id: str, conversation_id: str) -> dict[str, Any] | None:
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT id, user_id, course_id, title, conversation_type, "
                    "created_at, updated_at FROM ai_conversations "
                    "WHERE id = :conversation_id AND user_id = :user_id"
                ),
                {"conversation_id": conversation_id, "user_id": user_id},
            )
            row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def insert_message(
        self,
        *,
        conversation_id: str,
        role: str,
        content: str,
        model_name: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: int | None = None,
    ) -> dict[str, Any]:
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "INSERT INTO ai_messages (conversation_id, role, content, model_name, "
                    "prompt_tokens, completion_tokens, latency_ms) "
                    "VALUES (:conversation_id, :role, :content, :model_name, "
                    ":prompt_tokens, :completion_tokens, :latency_ms) "
                    "RETURNING id, conversation_id, role, content, model_name, "
                    "prompt_tokens, completion_tokens, latency_ms, created_at"
                ),
                {
                    "conversation_id": conversation_id,
                    "role": role,
                    "content": content,
                    "model_name": model_name,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "latency_ms": latency_ms,
                },
            )
            row = result.mappings().one()
        return dict(row)

    async def list_messages(
        self, user_id: str, conversation_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """History oldest-first; conversation ownership enforced via join."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT m.id, m.conversation_id, m.role, m.content, m.model_name, "
                    "m.prompt_tokens, m.completion_tokens, m.latency_ms, m.created_at "
                    "FROM ai_messages m JOIN ai_conversations c ON c.id = m.conversation_id "
                    "WHERE m.conversation_id = :conversation_id AND c.user_id = :user_id "
                    "ORDER BY m.created_at ASC LIMIT :limit"
                ),
                {"conversation_id": conversation_id, "user_id": user_id, "limit": limit},
            )
            rows = result.mappings().all()
        return [dict(row) for row in rows]
