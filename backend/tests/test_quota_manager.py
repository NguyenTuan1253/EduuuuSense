"""Unit tests for TASK-M4-02: atomic quota reservation, refund, fail-closed (US 2.21)."""

from typing import Any

import pytest

from app.repositories.quota_repo import QuotaPolicyRepository
from app.services.quota_service import (
    QuotaExhaustedError,
    QuotaService,
    QuotaUnavailableError,
)

USER_ID = "q1q1q1q1-q1q1-q1q1-q1q1-q1q1q1q1q1q1"


class FakeRedis:
    """In-memory Redis subset: incr/decr/expire/get with failure injection."""

    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.fail = False
        self.call_log: list[str] = []

    def incr(self, key: str) -> int:
        if self.fail:
            raise ConnectionError("redis down")
        self.call_log.append(f"incr:{key}")
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    def decr(self, key: str) -> int:
        if self.fail:
            raise ConnectionError("redis down")
        self.call_log.append(f"decr:{key}")
        self.counters[key] = self.counters.get(key, 0) - 1
        return self.counters[key]

    def expire(self, key: str, seconds: int) -> bool:
        self.ttls[key] = seconds
        return True

    def get(self, key: str) -> Any:
        if self.fail:
            raise ConnectionError("redis down")
        return self.counters.get(key, 0)


class FakeQuotaPolicyRepository(QuotaPolicyRepository):
    def __init__(self, policies: dict[str, dict[str, Any]] | None = None) -> None:
        self.policies = policies or {
            "free": {"request_limit_per_day": 10, "token_limit_per_day": None, "warning_threshold_percent": 80},
            "pro": {"request_limit_per_day": 150, "token_limit_per_day": None, "warning_threshold_percent": 80},
        }

    async def get_policy_for_plan(self, plan_code: str) -> dict[str, Any] | None:
        return self.policies.get(plan_code)


def make_service(redis: FakeRedis | None = None) -> tuple[QuotaService, FakeRedis]:
    redis = redis or FakeRedis()
    return QuotaService(redis=redis, quota_repo=FakeQuotaPolicyRepository()), redis


@pytest.mark.asyncio
async def test_reserve_increments_and_sets_expiry() -> None:
    service, redis = make_service()
    result = await service.reserve(USER_ID, "free")

    assert result["allowed"] is True
    assert result["used"] == 1
    assert result["remaining"] == 9
    assert result["quota_warning"] is False
    key = f"edusense:quota:{USER_ID}:{result_used_date()}:requests"
    assert redis.ttls.get(key, 0) > 0  # namespaced key always expires


def result_used_date() -> str:
    from datetime import UTC, datetime

    return datetime.now(tz=UTC).date().isoformat()


@pytest.mark.asyncio
async def test_exhausted_quota_rejects_and_rolls_back_counter() -> None:
    """The 11th free-plan request is rejected AND the counter is refunded."""
    service, redis = make_service()
    for _ in range(10):
        result = await service.reserve(USER_ID, "free")
    assert result["used"] == 10

    with pytest.raises(QuotaExhaustedError):
        await service.reserve(USER_ID, "free")

    key = f"edusense:quota:{USER_ID}:{result_used_date()}:requests"
    assert redis.counters[key] == 10  # rolled back, not 11


@pytest.mark.asyncio
async def test_warning_flag_at_80_percent_threshold() -> None:
    """Free plan (10/day, threshold 80%): warning from the 8th request on."""
    service, _ = make_service()
    flags: list[bool] = []
    for _ in range(10):
        result = await service.reserve(USER_ID, "free")
        flags.append(result["quota_warning"])

    assert flags[:7] == [False] * 7
    assert flags[7:] == [True] * 3


@pytest.mark.asyncio
async def test_refund_returns_a_reserved_request() -> None:
    """Provider failure path: refund decrements; counter never goes negative."""
    service, redis = make_service()
    await service.reserve(USER_ID, "pro")
    await service.reserve(USER_ID, "pro")

    await service.refund(USER_ID)

    key = f"edusense:quota:{USER_ID}:{result_used_date()}:requests"
    assert redis.counters[key] == 1

    await service.refund(USER_ID)
    await service.refund(USER_ID)  # extra refunds are no-ops
    assert redis.counters[key] == 0


@pytest.mark.asyncio
async def test_redis_outage_fails_closed_on_reserve_and_usage() -> None:
    """DECISION-CONFIRMED-05: unreachable Redis rejects the AI request."""
    redis = FakeRedis()
    redis.fail = True
    service, _ = make_service(redis)

    with pytest.raises(QuotaUnavailableError):
        await service.reserve(USER_ID, "free")
    with pytest.raises(QuotaUnavailableError):
        await service.usage(USER_ID, "free")


@pytest.mark.asyncio
async def test_unknown_plan_fails_closed() -> None:
    """No policy configured -> reject (never an implicit unlimited plan)."""
    service, _ = make_service()
    with pytest.raises(QuotaUnavailableError):
        await service.reserve(USER_ID, "enterprise")


@pytest.mark.asyncio
async def test_keys_are_namespaced_per_user_and_day() -> None:
    """Isolation: different users and days never share a counter."""
    service, redis = make_service()
    await service.reserve("user-a", "free")
    await service.reserve("user-b", "free")

    keys = [k for k in redis.counters if k.startswith("edusense:quota:")]
    assert len(keys) == 2
    assert all("user-a" in k or "user-b" in k for k in keys)
