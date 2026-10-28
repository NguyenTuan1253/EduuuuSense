"""AI usage persistence: SQL for ai_usage_logs (Invariant 4).

Rows carry counters and outcomes only — never prompts, completions, document
content, or user messages (security rule: no AI content in logs).
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class AiUsageRepository:
    """SQL access for ai_usage_logs. No business logic lives here."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_usage(self, record: dict[str, Any]) -> str:
        """Insert one usage record; returns its id."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "INSERT INTO ai_usage_logs "
                    "(user_id, conversation_id, message_id, feature, provider, model_name, "
                    "prompt_tokens, completion_tokens, total_tokens, estimated_cost, "
                    "latency_ms, request_status, error_message) "
                    "VALUES (:user_id, :conversation_id, :message_id, :feature, :provider, "
                    ":model_name, :prompt_tokens, :completion_tokens, :total_tokens, "
                    ":estimated_cost, :latency_ms, :request_status, :error_message) "
                    "RETURNING id"
                ),
                {
                    "user_id": record["user_id"],
                    "conversation_id": record.get("conversation_id"),
                    "message_id": record.get("message_id"),
                    "feature": record["feature"],
                    "provider": record.get("provider", "google"),
                    "model_name": record["model_name"],
                    "prompt_tokens": int(record.get("prompt_tokens", 0)),
                    "completion_tokens": int(record.get("completion_tokens", 0)),
                    "total_tokens": int(record.get("total_tokens", 0)),
                    "estimated_cost": record.get("estimated_cost", 0),
                    "latency_ms": record.get("latency_ms"),
                    "request_status": record.get("request_status", "success"),
                    "error_message": record.get("error_message"),
                },
            )
            usage_id = str(result.scalar_one())
        return usage_id

    async def summarize_usage(self, user_id: str, day: str) -> dict[str, int]:
        """Aggregate one user's token usage for a day (admin/dashboards later)."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT COALESCE(sum(total_tokens), 0) AS total_tokens, "
                    "count(*) AS requests "
                    "FROM ai_usage_logs "
                    "WHERE user_id = :user_id "
                    "AND created_at::date = :day::date"
                ),
                {"user_id": user_id, "day": day},
            )
            row = result.mappings().one()
        return {"total_tokens": int(row["total_tokens"]), "requests": int(row["requests"])}
