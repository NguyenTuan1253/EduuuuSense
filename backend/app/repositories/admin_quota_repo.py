"""Admin AI quota persistence: usage analytics + policy configuration (US 2.23)."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class AdminQuotaRepository:
    """SQL access for admin quota views/policies. No business logic here."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def usage_by_day(self, *, day: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Per-user per-day AI usage totals (tokens, requests, features)."""
        params: dict[str, Any] = {"limit": limit}
        day_filter = ""
        if day:
            day_filter = "WHERE l.created_at::date = :day::date"
            params["day"] = day
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT l.user_id, u.email, l.created_at::date AS usage_day, "
                    "count(*) AS requests, sum(l.total_tokens) AS total_tokens, "
                    "sum(l.prompt_tokens) AS prompt_tokens, "
                    "sum(l.completion_tokens) AS completion_tokens, "
                    "count(*) FILTER (WHERE l.request_status <> 'success') AS failed_requests "
                    f"FROM ai_usage_logs l JOIN users u ON u.id = l.user_id "
                    f"{day_filter} "
                    "GROUP BY l.user_id, u.email, l.created_at::date "
                    "ORDER BY usage_day DESC, total_tokens DESC LIMIT :limit"
                ),
                params,
            )
            rows = result.mappings().all()
        return [dict(row) for row in rows]

    async def usage_by_feature(self, *, day: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        day_filter = ""
        if day:
            day_filter = "WHERE created_at::date = :day::date"
            params["day"] = day
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT feature, count(*) AS requests, sum(total_tokens) AS total_tokens "
                    f"FROM ai_usage_logs {day_filter} "
                    "GROUP BY feature ORDER BY total_tokens DESC"
                ),
                params,
            )
            rows = result.mappings().all()
        return [dict(row) for row in rows]

    async def get_policy(self, plan_code: str) -> dict[str, Any] | None:
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT qp.id, sp.code AS plan_code, sp.name AS plan_name, "
                    "qp.request_limit_per_day, qp.token_limit_per_day, "
                    "qp.warning_threshold_percent, qp.updated_at "
                    "FROM quota_policies qp JOIN subscription_plans sp ON sp.id = qp.plan_id "
                    "WHERE sp.code = :plan_code"
                ),
                {"plan_code": plan_code},
            )
            row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def update_policy(
        self,
        *,
        plan_code: str,
        admin_id: str,
        request_limit_per_day: int,
        token_limit_per_day: int | None,
        warning_threshold_percent: int,
    ) -> dict[str, Any] | None:
        """Update a plan's policy (Admin action; audited via updated_by)."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "UPDATE quota_policies qp SET "
                    "request_limit_per_day = :request_limit, "
                    "token_limit_per_day = :token_limit, "
                    "warning_threshold_percent = :warning, "
                    "updated_by = :admin_id, updated_at = CURRENT_TIMESTAMP "
                    "FROM subscription_plans sp "
                    "WHERE qp.plan_id = sp.id AND sp.code = :plan_code "
                    "RETURNING qp.request_limit_per_day, qp.token_limit_per_day, "
                    "qp.warning_threshold_percent, qp.updated_at"
                ),
                {
                    "plan_code": plan_code,
                    "admin_id": admin_id,
                    "request_limit": request_limit_per_day,
                    "token_limit": token_limit_per_day,
                    "warning": warning_threshold_percent,
                },
            )
            row = result.mappings().one_or_none()
        return dict(row) if row else None
