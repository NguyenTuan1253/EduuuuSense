"""General AI chat business logic (US 2.12): conversations, history, replies.

Flow per user message: quota reserve -> persist user message -> Gemini
generation through the shared adapter (usage logged via hook) -> persist
assistant message. Provider failure refunds the reservation. Fail-closed
quota and generic provider errors are mapped by the router.
"""

from typing import Any

from app.providers.circuit_breaker import CircuitOpenError
from app.providers.gemini_adapter import GeminiAdapter
from app.providers.usage_logger import UsageLogger
from app.repositories.chat_repo import ChatRepository
from app.services.quota_service import QuotaService


class ConversationNotFoundError(Exception):
    """Raised when a conversation does not exist for the requesting owner."""


class ChatService:
    """Chat use cases for the authenticated owner."""

    SYSTEM_PROMPT = (
        "You are EduSense's study assistant for university students. "
        "Answer clearly and concisely. You have no tools and cannot take actions."
    )

    def __init__(
        self,
        chat_repo: ChatRepository,
        quota_service: QuotaService,
        gemini: GeminiAdapter,
        usage_logger: UsageLogger,
        plan_code: str = "free",
    ) -> None:
        self._chat_repo = chat_repo
        self._quota = quota_service
        self._gemini = gemini
        self._usage_logger = usage_logger
        self._plan_code = plan_code

    @classmethod
    def from_session(
        cls, session: Any, plan_code: str = "free", redis_client: Any | None = None
    ) -> "ChatService":
        """Compose the full chat stack over one session (production wiring)."""
        from app.core.config import settings
        from app.repositories.ai_usage_repo import AiUsageRepository
        from app.repositories.quota_repo import QuotaPolicyRepository

        quota = QuotaService(
            redis=redis_client, quota_repo=QuotaPolicyRepository(session)
        ) if redis_client is not None else QuotaService(
            redis=_lazy_redis(settings.redis_url),
            quota_repo=QuotaPolicyRepository(session),
        )
        return cls(
            chat_repo=ChatRepository(session),
            quota_service=quota,
            gemini=GeminiAdapter(),
            usage_logger=UsageLogger(AiUsageRepository(session)),
            plan_code=plan_code,
        )

    async def create_conversation(
        self, user_id: str, *, title: str | None, course_id: str | None = None
    ) -> dict[str, Any]:
        return await self._chat_repo.create_conversation(
            user_id=user_id, title=title, course_id=course_id
        )

    async def list_conversations(self, user_id: str) -> list[dict[str, Any]]:
        return await self._chat_repo.list_conversations(user_id)

    async def list_messages(self, user_id: str, conversation_id: str) -> list[dict[str, Any]]:
        conversation = await self._chat_repo.get_conversation(user_id, conversation_id)
        if conversation is None:
            raise ConversationNotFoundError("Conversation not found.")
        return await self._chat_repo.list_messages(user_id, conversation_id)

    async def send_message(self, user_id: str, conversation_id: str, content: str) -> dict[str, Any]:
        """One chat turn. Raises QuotaExhaustedError/QuotaUnavailableError and
        ChatProviderError per the router's mapping."""
        conversation = await self._chat_repo.get_conversation(user_id, conversation_id)
        if conversation is None:
            raise ConversationNotFoundError("Conversation not found.")

        reservation = await self._quota.reserve(user_id, self._plan_code)

        user_message = await self._chat_repo.insert_message(
            conversation_id=conversation_id, role="user", content=content.strip()
        )

        try:
            result = await self._gemini.generate(
                content.strip(),
                system=self.SYSTEM_PROMPT,
                usage_context={
                    "user_id": user_id,
                    "feature": "chat",
                    "conversation_id": conversation_id,
                },
            )
        except CircuitOpenError as exc:
            await self._quota.refund(user_id)
            raise ChatProviderError("The AI service is temporarily unavailable.") from exc
        except Exception as exc:  # noqa: BLE001 — mapped, quota refunded
            await self._quota.refund(user_id)
            raise ChatProviderError("The AI service is temporarily unavailable.") from exc

        assistant_message = await self._chat_repo.insert_message(
            conversation_id=conversation_id,
            role="assistant",
            content=result.text.strip() or "(no response)",
            model_name=result.model_name,
            prompt_tokens=result.usage["prompt_tokens"],
            completion_tokens=result.usage["completion_tokens"],
            latency_ms=result.latency_ms,
        )
        return {
            "conversation_id": conversation_id,
            "user_message": user_message,
            "assistant_message": assistant_message,
            "ai_generated": True,  # US 2.12: responses are labeled AI-generated
            "quota": {
                "remaining": reservation["remaining"],
                "quota_warning": reservation["quota_warning"],
            },
        }


class ChatProviderError(Exception):
    """Raised when the AI provider cannot serve the request (after retries)."""


def _lazy_redis(url: str) -> Any:
    """Sync Redis client for the quota gate (created once, reused)."""
    from redis import Redis

    return Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
