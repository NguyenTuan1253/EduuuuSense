"""Admin AI usage analytics & quota policy configuration (US 2.23)."""

from typing import Any

from app.repositories.admin_quota_repo import AdminQuotaRepository


class PolicyNotFoundError(Exception):
    """Raised when a plan has no quota policy row."""


class AdminQuotaService:
    """Admin analytics and policy updates."""

    def __init__(self, admin_quota_repo: AdminQuotaRepository) -> None:
        self._repo = admin_quota_repo

    @classmethod
    def from_session(cls, session: Any) -> "AdminQuotaService":
        """Compose over one session."""
        return cls(admin_quota_repo=AdminQuotaRepository(session))

    async def usage_by_day(self, *, day: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return await self._repo.usage_by_day(day=day, limit=min(limit, 100))

    async def usage_by_feature(self, *, day: str | None = None) -> list[dict[str, Any]]:
        return await self._repo.usage_by_feature(day=day)

    async def get_policy(self, plan_code: str) -> dict[str, Any]:
        policy = await self._repo.get_policy(plan_code)
        if policy is None:
            raise PolicyNotFoundError("No quota policy for this plan.")
        return policy

    async def update_policy(
        self,
        admin_id: str,
        plan_code: str,
        *,
        request_limit_per_day: int,
        token_limit_per_day: int | None,
        warning_threshold_percent: int,
    ) -> dict[str, Any]:
        """Update the policy; changes apply to NEW Redis keys (next day) and
        immediate reads re-check limits, so tightening takes effect at once."""
        if request_limit_per_day < 0:
            raise ValueError("request_limit_per_day must be >= 0.")
        if not 1 <= warning_threshold_percent <= 100:
            raise ValueError("warning_threshold_percent must be within 1..100.")
        updated = await self._repo.update_policy(
            plan_code=plan_code,
            admin_id=admin_id,
            request_limit_per_day=request_limit_per_day,
            token_limit_per_day=token_limit_per_day,
            warning_threshold_percent=warning_threshold_percent,
        )
        if updated is None:
            raise PolicyNotFoundError("No quota policy for this plan.")
        return {"plan_code": plan_code, **updated}
