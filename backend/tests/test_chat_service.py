"""Unit tests for TASK-M4-04: AI chatbot conversations & history (US 2.12)."""

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.v1.chat import get_chat_service
from app.core.security import create_access_token
from app.main import app
from app.repositories.chat_repo import ChatRepository
from app.providers.gemini_adapter import GeminiAdapter
from app.providers.usage_logger import UsageLogger
from app.repositories.ai_usage_repo import AiUsageRepository
from app.services.chat_service import ChatProviderError, ChatService
from app.services.quota_service import QuotaExhaustedError, QuotaService

USER_ID = "c7c7c7c7-c7c7-c7c7-c7c7-c7c7c7c7c7c7"
OTHER_ID = "d8d8d8d8-d8d8-d8d8-d8d8-d8d8d8d8d8d8"
CONV_ID = "e9e9e9e9-e9e9-e9e9-e9e9-e9e9e9e9e9e9"


class FakeChatRepository(ChatRepository):
    def __init__(self, session: Any = None) -> None:
        self.conversations: dict[str, dict[str, Any]] = {CONV_ID: {"id": CONV_ID, "user_id": USER_ID, "title": "Study", "conversation_type": "general"}}
        self.messages: list[dict[str, Any]] = []

    async def create_conversation(self, **kwargs: Any) -> dict[str, Any]:
        conv_id = f"conv-{len(self.conversations) + 1}"
        row = {"id": conv_id, **kwargs}
        self.conversations[conv_id] = row
        return dict(row)

    async def list_conversations(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return [dict(c) for c in self.conversations.values() if c["user_id"] == user_id]

    async def get_conversation(self, user_id: str, conversation_id: str) -> dict[str, Any] | None:
        row = self.conversations.get(conversation_id)
        if row and row["user_id"] == user_id:
            return dict(row)
        return None

    async def insert_message(self, **kwargs: Any) -> dict[str, Any]:
        row = {"id": f"msg-{len(self.messages) + 1}", **kwargs}
        self.messages.append(row)
        return dict(row)

    async def list_messages(self, user_id: str, conversation_id: str, limit: int = 100) -> list[dict[str, Any]]:
        conv = await self.get_conversation(user_id, conversation_id)
        if conv is None:
            return []
        return [dict(m) for m in self.messages if m["conversation_id"] == conversation_id]


class FakeQuotaService(QuotaService):
    def __init__(self, *, exhausted: bool = False, unavailable: bool = False) -> None:
        self.exhausted = exhausted
        self.unavailable = unavailable
        self.refunded = 0

    async def reserve(self, user_id: str, plan_code: str) -> dict[str, Any]:
        if self.unavailable:
            from app.services.quota_service import QuotaUnavailableError
            raise QuotaUnavailableError("Quota service is unavailable.")
        if self.exhausted:
            raise QuotaExhaustedError("Daily AI request quota exceeded.")
        return {"allowed": True, "used": 1, "limit": 10, "remaining": 9, "quota_warning": False}

    async def refund(self, user_id: str) -> None:
        self.refunded += 1


class FailingGemini(GeminiAdapter):
    def __init__(self) -> None:
        super().__init__(client=object())

    async def generate(self, prompt: str, *, system: str | None = None, usage_context: dict[str, Any] | None = None):  # type: ignore[override]
        from app.providers.circuit_breaker import CircuitOpenError
        raise CircuitOpenError("open")


class WorkingGemini(GeminiAdapter):
    def __init__(self) -> None:
        super().__init__(client=object())
        self.prompts: list[str] = []

    async def generate(self, prompt: str, *, system: str | None = None, usage_context: dict[str, Any] | None = None):  # type: ignore[override]
        self.prompts.append(prompt)
        return type(
            "R", (), {
                "text": "Vectors are lists of numbers representing meaning.",
                "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
                "latency_ms": 120,
                "model_name": "gemini-test",
            },
        )()


def make_service(
    *, gemini: GeminiAdapter | None = None, quota: FakeQuotaService | None = None
) -> tuple[ChatService, FakeChatRepository, FakeQuotaService]:
    repo = FakeChatRepository()
    quota = quota or FakeQuotaService()
    service = ChatService(
        chat_repo=repo,
        quota_service=quota,
        gemini=gemini or WorkingGemini(),
        usage_logger=UsageLogger(AiUsageRepository(None))  # type: ignore[arg-type]
    )
    return service, repo, quota


@pytest_asyncio.fixture
async def client_and_service() -> AsyncGenerator[tuple[AsyncClient, ChatService, FakeQuotaService], None]:
    service, _, quota = make_service()

    async def override_chat_service() -> ChatService:
        return service

    app.dependency_overrides[get_chat_service] = override_chat_service
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, service, quota
    app.dependency_overrides.clear()


def auth(user_id: str = USER_ID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


@pytest.mark.asyncio
async def test_send_message_persists_both_roles_and_labels_ai(
    client_and_service: tuple[AsyncClient, ChatService, FakeQuotaService],
) -> None:
    client, service, _ = client_and_service
    response = await client.post(
        f"/api/v1/chat/conversations/{CONV_ID}/messages",
        json={"content": "What is a vector?"},
        headers=auth(),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["ai_generated"] is True  # US 2.12 labeling
    assert data["user_message"]["role"] == "user"
    assert data["assistant_message"]["role"] == "assistant"
    assert data["assistant_message"]["model_name"] == "gemini-test"
    history = await service.list_messages(USER_ID, CONV_ID)
    assert [m["role"] for m in history] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_send_message_strips_blank_and_rejects_empty(
    client_and_service: tuple[AsyncClient, ChatService, FakeQuotaService],
) -> None:
    client, _, _ = client_and_service
    empty = await client.post(
        f"/api/v1/chat/conversations/{CONV_ID}/messages", json={"content": "   "}, headers=auth()
    )
    assert empty.status_code == 422


@pytest.mark.asyncio
async def test_foreign_conversation_is_404(
    client_and_service: tuple[AsyncClient, ChatService, FakeQuotaService],
) -> None:
    client, _, _ = client_and_service
    response = await client.post(
        f"/api/v1/chat/conversations/{CONV_ID}/messages",
        json={"content": "hi"},
        headers=auth(OTHER_ID),
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_quota_exhausted_maps_to_429(
    client_and_service: tuple[AsyncClient, ChatService, FakeQuotaService],
) -> None:
    client, _, quota = client_and_service
    quota.exhausted = True
    response = await client.post(
        f"/api/v1/chat/conversations/{CONV_ID}/messages",
        json={"content": "hi"},
        headers=auth(),
    )
    assert response.status_code == 429


@pytest.mark.asyncio
async def test_quota_unavailable_fails_closed_with_503(
    client_and_service: tuple[AsyncClient, ChatService, FakeQuotaService],
) -> None:
    client, _, quota = client_and_service
    quota.unavailable = True
    response = await client.post(
        f"/api/v1/chat/conversations/{CONV_ID}/messages",
        json={"content": "hi"},
        headers=auth(),
    )
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_provider_failure_refunds_quota(
    client_and_service: tuple[AsyncClient, ChatService, FakeQuotaService],
) -> None:
    """DECISION-CONFIRMED-05: refund the reservation when the provider fails."""
    failing_service, failing_repo, failing_quota = make_service(gemini=FailingGemini())

    with pytest.raises(ChatProviderError):
        await failing_service.send_message(USER_ID, CONV_ID, "hi")

    assert failing_quota.refunded == 1
    assert failing_repo.messages[-1]["role"] == "user"  # user msg persisted


@pytest.mark.asyncio
async def test_create_and_list_conversations_owner_scoped(
    client_and_service: tuple[AsyncClient, ChatService, FakeQuotaService],
) -> None:
    client, _, _ = client_and_service
    created = await client.post(
        "/api/v1/chat/conversations", json={"title": "New Chat"}, headers=auth()
    )
    assert created.status_code == 201

    listed = await client.get("/api/v1/chat/conversations", headers=auth())
    foreign = await client.get("/api/v1/chat/conversations", headers=auth(OTHER_ID))

    assert len(listed.json()["data"]["conversations"]) >= 1
    assert foreign.json()["data"]["conversations"] == []


@pytest.mark.asyncio
async def test_history_requires_ownership(
    client_and_service: tuple[AsyncClient, ChatService, FakeQuotaService],
) -> None:
    client, service, _ = client_and_service
    await service.send_message(USER_ID, CONV_ID, "hello")

    own = await client.get(f"/api/v1/chat/conversations/{CONV_ID}/messages", headers=auth())
    foreign = await client.get(
        f"/api/v1/chat/conversations/{CONV_ID}/messages", headers=auth(OTHER_ID)
    )

    assert own.status_code == 200
    assert len(own.json()["data"]["messages"]) == 2
    assert foreign.status_code == 404
