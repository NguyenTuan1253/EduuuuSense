"""Quota policy persistence: reads plan limits from quota_policies."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class QuotaPolicyRepository:
    """SQL access for quota_policies. No business logic lives here."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_policy_for_plan(self, plan_code: str) -> dict[str, Any] | None:
        """Fetch the daily request/token limits and warning threshold for a plan."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT qp.request_limit_per_day, qp.token_limit_per_day, "
                    "qp.warning_threshold_percent "
                    "FROM quota_policies qp "
                    "JOIN subscription_plans sp ON sp.id = qp.plan_id "
                    "WHERE sp.code = :plan_code"
                ),
                {"plan_code": plan_code},
            )
            row = result.mappings().one_or_none()
        return dict(row) if row else None
