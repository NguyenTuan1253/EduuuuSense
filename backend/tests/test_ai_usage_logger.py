"""Unit tests for TASK-M4-03: AI usage logging & token tracking (Invariant 4)."""

from typing import Any

import pytest

from app.providers.usage_logger import UsageLogger
from app.repositories.ai_usage_repo import AiUsageRepository

USER_ID = "u2u2u2u2-u2u2-u2u2-u2u2-u2u2u2u2u2u2"


class FakeAiUsageRepository(AiUsageRepository):
    def __init__(self, fail: bool = False) -> None:
        self.rows: list[dict[str, Any]] = []
        self.fail = fail

    async def insert_usage(self, record: dict[str, Any]) -> str:
        if self.fail:
            raise RuntimeError("database unavailable")
        self.rows.append(dict(record))
        return f"usage-{len(self.rows)}"


@pytest.mark.asyncio
async def test_record_persists_token_counters() -> None:
    repo = FakeAiUsageRepository()
    logger = UsageLogger(repo)

    await logger.record(
        user_id=USER_ID,
        feature="chat",
        model_name="gemini-test",
        prompt_tokens=120,
        completion_tokens=80,
        total_tokens=200,
        latency_ms=850,
    )

    row = repo.rows[0]
    assert row["feature"] == "chat"
    assert row["prompt_tokens"] == 120
    assert row["total_tokens"] == 200
    assert row["request_status"] == "success"
    assert row["provider"] == "google"


@pytest.mark.asyncio
async def test_record_failure_degrades_without_raising() -> None:
    """A usage-logging outage never breaks the user-facing AI flow."""
    repo = FakeAiUsageRepository(fail=True)
    logger = UsageLogger(repo)

    await logger.record(
        user_id=USER_ID, feature="chat", model_name="gemini-test", total_tokens=10
    )  # must not raise
    assert repo.rows == []


@pytest.mark.asyncio
async def test_no_prompt_or_completion_content_ever_persisted() -> None:
    """Security rule: log rows carry counters/ids only, never AI content."""
    repo = FakeAiUsageRepository()
    logger = UsageLogger(repo)

    await logger.record(
        user_id=USER_ID,
        feature="chat",
        model_name="gemini-test",
        prompt_tokens=5,
        completion_tokens=5,
        total_tokens=10,
        error_message="rate limited",
        request_status="error",
    )

    serialized = str(repo.rows)
    assert "prompt" not in repo.rows[0]  # no prompt text field exists at all
    assert repo.rows[0]["error_message"] == "rate limited"  # outcome only
    assert "content" not in serialized


@pytest.mark.asyncio
async def test_error_rows_require_error_message_and_non_negative_counters() -> None:
    repo = FakeAiUsageRepository()
    logger = UsageLogger(repo)

    await logger.record(
        user_id=USER_ID,
        feature="summarization",
        model_name="gemini-test",
        prompt_tokens=-5,  # defensive: clamped
        completion_tokens=0,
        total_tokens=0,
        request_status="error",
    )

    row = repo.rows[0]
    assert row["prompt_tokens"] == 0
    assert row["error_message"] == "unspecified provider error"
    assert row["estimated_cost"] == 0  # no pricing configured (PO decision pending)


@pytest.mark.asyncio
async def test_adapter_hook_wires_gemini_payloads() -> None:
    repo = FakeAiUsageRepository()
    logger = UsageLogger(repo)

    hook = logger.adapter_hook(USER_ID, feature="rag_chat", conversation_id="conv-1")
    await hook(
        {
            "model_name": "gemini-test",
            "prompt_tokens": 10,
            "completion_tokens": 4,
            "total_tokens": 14,
            "latency_ms": 120,
            "request_status": "success",
        }
    )

    row = repo.rows[0]
    assert row["user_id"] == USER_ID
    assert row["feature"] == "rag_chat"
    assert row["conversation_id"] == "conv-1"
    assert row["total_tokens"] == 14
