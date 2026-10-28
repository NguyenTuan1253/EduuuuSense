"""Admin system storage & resource consumption reports (US 2.24)."""

from typing import Any

from app.repositories.system_report_repo import SystemReportRepository


class SystemReportService:
    """System-wide reporting for administrators."""

    def __init__(self, system_report_repo: SystemReportRepository) -> None:
        self._repo = system_report_repo

    @classmethod
    def from_session(cls, session: Any) -> "SystemReportService":
        """Compose over one session."""
        return cls(system_report_repo=SystemReportRepository(session))

    async def storage_report(self, *, limit: int = 100) -> dict[str, Any]:
        """Totals plus a per-user breakdown (largest consumers first)."""
        totals = await self._repo.storage_totals()
        by_user = await self._repo.storage_by_user(limit=min(limit, 200))
        return {
            "totals": totals,
            "by_user": [
                {k: (str(v) if v is not None else None) for k, v in row.items()} for row in by_user
            ],
        }

    async def resource_report(self) -> dict[str, Any]:
        return await self._repo.resource_totals()
