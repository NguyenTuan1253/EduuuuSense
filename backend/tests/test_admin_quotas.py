"""Unit tests for TASK-M6-05: admin AI usage analytics & quota policies (US 2.23)."""

from typing import Any

import pytest

from app.repositories.admin_quota_repo import AdminQuotaRepository
from app.services.admin_quota_service import AdminQuotaService, PolicyNotFoundError

ADMIN_ID = "aq1-aq1-aq1-aq1-aq1aq1aq1aq1"


class FakeAdminQuotaRepository(AdminQuotaRepository):
    def __init__(self, session: Any = None) -> None:
        self.day_rows = [
            {
                "user_id": "u1",
                "email": "a@example.com",
                "usage_day": "2026-10-28",
                "requests": 12,
                "total_tokens": 900,
                "prompt_tokens": 500,
                "completion_tokens": 400,
                "failed_requests": 1,
            }
        ]
        self.feature_rows = [{"feature": "chat", "requests": 8, "total_tokens": 600}]
        self.policies = {
            "free": {
                "id": "qp-1",
                "plan_code": "free",
                "plan_name": "Free",
                "request_limit_per_day": 10,
                "token_limit_per_day": None,
                "warning_threshold_percent": 80,
                "updated_at": "2026-10-01T00:00:00+00:00",
            }
        }

    async def usage_by_day(self, *, day: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return [dict(r) for r in self.day_rows]

    async def usage_by_feature(self, *, day: str | None = None) -> list[dict[str, Any]]:
        return [dict(r) for r in self.feature_rows]

    async def get_policy(self, plan_code: str) -> dict[str, Any] | None:
        return dict(self.policies[plan_code]) if plan_code in self.policies else None

    async def update_policy(self, **kwargs: Any) -> dict[str, Any] | None:
        if kwargs["plan_code"] not in self.policies:
            return None
        self.policies[kwargs["plan_code"]].update(
            {
                "request_limit_per_day": kwargs["request_limit_per_day"],
                "token_limit_per_day": kwargs["token_limit_per_day"],
                "warning_threshold_percent": kwargs["warning_threshold_percent"],
            }
        )
        return {
            "request_limit_per_day": kwargs["request_limit_per_day"],
            "token_limit_per_day": kwargs["token_limit_per_day"],
            "warning_threshold_percent": kwargs["warning_threshold_percent"],
            "updated_at": "2026-10-28T00:00:00+00:00",
        }


def make_service() -> tuple[AdminQuotaService, FakeAdminQuotaRepository]:
    repo = FakeAdminQuotaRepository()
    return AdminQuotaService(admin_quota_repo=repo), repo


@pytest.mark.asyncio
async def test_usage_by_day_returns_totals() -> None:
    service, _ = make_service()
    rows = await service.usage_by_day(day="2026-10-28")

    assert rows[0]["requests"] == 12
    assert rows[0]["total_tokens"] == 900
    assert rows[0]["failed_requests"] == 1


@pytest.mark.asyncio
async def test_usage_by_feature() -> None:
    service, _ = make_service()
    rows = await service.usage_by_feature()

    assert rows[0]["feature"] == "chat"
    assert rows[0]["total_tokens"] == 600


@pytest.mark.asyncio
async def test_get_policy_and_update_policy() -> None:
    service, repo = make_service()

    policy = await service.get_policy("free")
    assert policy["request_limit_per_day"] == 10

    updated = await service.update_policy(
        ADMIN_ID, "free", request_limit_per_day=20, token_limit_per_day=None, warning_threshold_percent=90
    )
    assert updated["request_limit_per_day"] == 20
    assert repo.policies["free"]["warning_threshold_percent"] == 90


@pytest.mark.asyncio
async def test_update_unknown_plan_raises_not_found() -> None:
    service, _ = make_service()

    with pytest.raises(PolicyNotFoundError):
        await service.update_policy(
            ADMIN_ID, "enterprise", request_limit_per_day=5, token_limit_per_day=None, warning_threshold_percent=80
        )


@pytest.mark.asyncio
async def test_invalid_policy_values_rejected() -> None:
    service, _ = make_service()

    with pytest.raises(ValueError):
        await service.update_policy(
            ADMIN_ID, "free", request_limit_per_day=-1, token_limit_per_day=None, warning_threshold_percent=80
        )
    with pytest.raises(ValueError):
        await service.update_policy(
            ADMIN_ID, "free", request_limit_per_day=10, token_limit_per_day=None, warning_threshold_percent=101
        )


@pytest.mark.asyncio
async def test_missing_policy_read_raises_not_found() -> None:
    service, _ = make_service()

    with pytest.raises(PolicyNotFoundError):
        await service.get_policy("enterprise")
