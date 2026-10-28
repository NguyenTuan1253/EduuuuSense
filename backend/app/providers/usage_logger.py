"""Usage logger bridging provider adapters to ai_usage_logs (Invariant 4).

Every provider call reports tokens/latency/outcome through this logger. Two
hard rules:
- Log payloads contain counters and identifiers ONLY — never prompts, model
  outputs, or user content.
- A logging failure must never break the user-facing AI flow: it is degraded
  to an error-level log line (deliberate, disclosed degradation — the audit
  row is lost, the answer is kept).
"""

import logging
from typing import Any

from app.repositories.ai_usage_repo import AiUsageRepository

logger = logging.getLogger("edusense.ai.usage")

# No provider pricing is configured in any source document; estimated_cost
# stays 0 until the PO approves a rate table (proposed follow-up).
DEFAULT_ESTIMATED_COST = 0


class UsageLogger:
    """Writes usage records via the repository; injectable for tests."""

    def __init__(self, repo: AiUsageRepository) -> None:
        self._repo = repo

    async def record(
        self,
        *,
        user_id: str,
        feature: str,
        model_name: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        latency_ms: int | None = None,
        request_status: str = "success",
        error_message: str | None = None,
        conversation_id: str | None = None,
        message_id: str | None = None,
    ) -> None:
        """Persist one usage row; degrades to a log line on failure."""
        if request_status != "success" and not error_message:
            error_message = "unspecified provider error"
        record = {
            "user_id": user_id,
            "conversation_id": conversation_id,
            "message_id": message_id,
            "feature": feature,
            "provider": "google",
            "model_name": model_name,
            "prompt_tokens": max(0, prompt_tokens),
            "completion_tokens": max(0, completion_tokens),
            "total_tokens": max(0, total_tokens),
            "estimated_cost": DEFAULT_ESTIMATED_COST,
            "latency_ms": latency_ms,
            "request_status": request_status,
            "error_message": error_message,
        }
        try:
            await self._repo.insert_usage(record)
        except Exception as exc:  # noqa: BLE001 — deliberate degradation
            logger.error(
                "AI usage logging failed (%s): feature=%s status=%s",
                type(exc).__name__,
                record["feature"],
                record["request_status"],
            )

    def adapter_hook(
        self, user_id: str, feature: str, conversation_id: str | None = None
    ) -> Any:
        """Build an on_usage callback for GeminiAdapter wired to this logger."""

        async def on_usage(payload: dict[str, Any]) -> None:
            await self.record(
                user_id=user_id,
                feature=feature,
                model_name=payload.get("model_name", "unknown"),
                prompt_tokens=int(payload.get("prompt_tokens", 0)),
                completion_tokens=int(payload.get("completion_tokens", 0)),
                total_tokens=int(payload.get("total_tokens", 0)),
                latency_ms=payload.get("latency_ms"),
                request_status=payload.get("request_status", "success"),
                conversation_id=conversation_id,
            )

        return on_usage
