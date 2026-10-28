"""Audit and rate-limit-evidence persistence (NFR04).

Owns all SQL for ``login_attempts`` (rate-limit evidence) and
``audit_security_events`` (security audit trail). No secrets, passwords, or
tokens are ever written to either table; ``details`` carries only safe,
non-sensitive context.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class AuditRepository:
    """SQL access for login-attempt evidence and security audit events."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_failure_state(
        self, identifier_hash: str, window_minutes: int
    ) -> dict[str, Any]:
        """Count failed attempts since the last success, inside the window.

        Returns ``failures_since_success`` and ``last_failure_at`` (None when
        no failures are on record). Attempts older than the window and failures
        followed by a successful login are ignored.
        """
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT "
                    "  count(*) FILTER ("
                    "    WHERE la.succeeded = FALSE"
                    "      AND la.attempted_at > CURRENT_TIMESTAMP - make_interval(mins => :window)"
                    "  ) AS failures, "
                    "  max(la.attempted_at) FILTER (WHERE la.succeeded = FALSE) AS last_failure_at "
                    "FROM login_attempts la "
                    "WHERE la.identifier_hash = :identifier_hash "
                    "  AND ("
                    "    la.attempted_at > COALESCE("
                    "      (SELECT max(prev.attempted_at) FROM login_attempts prev "
                    "       WHERE prev.identifier_hash = la.identifier_hash AND prev.succeeded = TRUE),"
                    "      '-infinity'::timestamptz"
                    "    )"
                    "  )"
                ),
                {"identifier_hash": identifier_hash, "window": window_minutes},
            )
            row = result.mappings().one()
        return {
            "failures": int(row["failures"] or 0),
            "last_failure_at": row["last_failure_at"],
        }

    async def record_attempt(
        self, *, identifier_hash: str, ip_address: str | None, succeeded: bool
    ) -> None:
        """Persist one login attempt as rate-limit evidence."""
        async with self._session.begin():
            await self._session.execute(
                text(
                    "INSERT INTO login_attempts (identifier_hash, ip_address, succeeded) "
                    "VALUES (:identifier_hash, :ip_address, :succeeded)"
                ),
                {
                    "identifier_hash": identifier_hash,
                    "ip_address": ip_address,
                    "succeeded": succeeded,
                },
            )

    async def record_security_event(
        self,
        *,
        event_type: str,
        severity: str = "info",
        actor_user_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Append one event to the security audit trail (no secrets allowed)."""
        async with self._session.begin():
            await self._session.execute(
                text(
                    "INSERT INTO audit_security_events "
                    "(actor_user_id, event_type, severity, ip_address, user_agent, details) "
                    "VALUES (:actor_user_id, :event_type, :severity, :ip_address, :user_agent, "
                    ":details::jsonb)"
                ),
                {
                    "actor_user_id": actor_user_id,
                    "event_type": event_type,
                    "severity": severity,
                    "ip_address": ip_address,
                    "user_agent": user_agent,
                    "details": details or {},
                },
            )

    async def get_failure_state_since(
        self, identifier_hash: str, window_minutes: int, since: datetime
    ) -> dict[str, Any]:
        """Test seam: same as get_failure_state but anchored to ``since``.

        Lets unit tests exercise window expiry deterministically without a
        live database clock.
        """
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT "
                    "  count(*) FILTER ("
                    "    WHERE la.succeeded = FALSE AND la.attempted_at > :since"
                    "  ) AS failures, "
                    "  max(la.attempted_at) FILTER (WHERE la.succeeded = FALSE) AS last_failure_at "
                    "FROM login_attempts la "
                    "WHERE la.identifier_hash = :identifier_hash "
                    "  AND ("
                    "    la.attempted_at > COALESCE("
                    "      (SELECT max(prev.attempted_at) FROM login_attempts prev "
                    "       WHERE prev.identifier_hash = la.identifier_hash AND prev.succeeded = TRUE),"
                    "      '-infinity'::timestamptz"
                    "    )"
                    "  )"
                ),
                {"identifier_hash": identifier_hash, "since": since},
            )
            row = result.mappings().one()
        return {
            "failures": int(row["failures"] or 0),
            "last_failure_at": row["last_failure_at"],
        }
