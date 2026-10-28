"""Atomic AI quota reservation with Redis fail-closed behavior (US 2.21, Invariant 7).

Rules (DECISION-CONFIRMED-05):
- Quota is reserved ATOMICALLY in Redis BEFORE any provider call; the counter
  key is namespaced per user per day and expires at midnight.
- Reservations are refunded when a provider call fails.
- If Redis is unreachable, requests are REJECTED (fail closed) — quota state in
  Redis is a real-time gate, never the only source of truth (policies live in
  PostgreSQL; usage counters are per-day and self-expire).
- Crossing the plan's warning threshold emits a ``quota_warning`` flag for the
  Notification module to surface — this service never writes notifications.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from app.repositories.quota_repo import QuotaPolicyRepository

REDIS_NAMESPACE = "edusense:quota"


class QuotaExhaustedError(Exception):
    """Raised when the plan's daily request limit is already used up."""


class QuotaUnavailableError(Exception):
    """Raised when Redis is unreachable — requests fail CLOSED."""


class RedisLike(Protocol):
    """Minimal Redis command surface the service relies on."""

    def incr(self, key: str) -> int: ...
    def decr(self, key: str) -> int: ...
    def expire(self, key: str, seconds: int) -> bool: ...
    def get(self, key: str) -> Any: ...


class QuotaService:
    """Daily request-quota gate. Redis client and policy repo are injected."""

    def __init__(self, redis: RedisLike, quota_repo: QuotaPolicyRepository) -> None:
        self._redis = redis
        self._quota_repo = quota_repo

    @staticmethod
    def _key(user_id: str, day: datetime) -> str:
        return f"{REDIS_NAMESPACE}:{user_id}:{day.astimezone(UTC).date().isoformat()}:requests"

    @staticmethod
    def _seconds_until_midnight(now: datetime) -> int:
        tomorrow = (now + timedelta(days=1)).astimezone(UTC).date()
        midnight = datetime.combine(tomorrow, datetime.min.time(), tzinfo=UTC)
        return max(60, int((midnight - now).total_seconds()))

    async def _policy(self, plan_code: str) -> dict[str, Any]:
        policy = await self._quota_repo.get_policy_for_plan(plan_code)
        if policy is None:
            # No policy configured for the plan: fail closed (unknown plan).
            raise QuotaUnavailableError(f"No quota policy configured for plan '{plan_code}'.")
        return policy

    async def reserve(self, user_id: str, plan_code: str) -> dict[str, Any]:
        """Atomically reserve one AI request. Returns usage state on success.

        Raises QuotaExhaustedError when over the limit; QuotaUnavailableError
        when Redis or policy lookup fails (fail closed — never allow open).
        """
        policy = await self._policy(plan_code)
        limit = int(policy["request_limit_per_day"])
        warning_threshold = int(policy["warning_threshold_percent"])

        key = self._key(user_id, datetime.now(tz=UTC))
        try:
            used = int(self._redis.incr(key))
            if used == 1:
                self._redis.expire(key, self._seconds_until_midnight(datetime.now(tz=UTC)))
        except Exception as exc:  # noqa: BLE001 — any Redis failure fails closed
            raise QuotaUnavailableError("Quota service is unavailable.") from exc

        if used > limit:
            # Give the rejected increment back so the counter stays accurate.
            try:
                self._redis.decr(key)
            except Exception:  # noqa: BLE001 — counter repair is best-effort
                pass
            raise QuotaExhaustedError("Daily AI request quota exceeded.")

        warning = limit > 0 and used >= limit * warning_threshold / 100
        return {
            "allowed": True,
            "used": used,
            "limit": limit,
            "remaining": max(0, limit - used),
            "quota_warning": warning,
        }

    async def refund(self, user_id: str) -> None:
        """Refund one reservation (provider call failed). Never goes below 0."""
        key = self._key(user_id, datetime.now(tz=UTC))
        try:
            used = int(self._redis.get(key) or 0)
            if used > 0:
                self._redis.decr(key)
        except Exception as exc:  # noqa: BLE001
            raise QuotaUnavailableError("Quota refund failed: Redis unavailable.") from exc

    async def usage(self, user_id: str, plan_code: str) -> dict[str, Any]:
        """Current usage snapshot (read-only; no reservation)."""
        policy = await self._policy(plan_code)
        limit = int(policy["request_limit_per_day"])
        key = self._key(user_id, datetime.now(tz=UTC))
        try:
            used = int(self._redis.get(key) or 0)
        except Exception as exc:  # noqa: BLE001
            raise QuotaUnavailableError("Quota service is unavailable.") from exc
        return {
            "used": used,
            "limit": limit,
            "remaining": max(0, limit - used),
            "quota_warning": limit > 0 and used >= limit * int(policy["warning_threshold_percent"]) / 100,
        }
