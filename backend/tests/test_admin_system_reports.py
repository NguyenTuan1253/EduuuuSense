"""Unit tests for TASK-M6-06: admin system reports (US 2.24)."""

from typing import Any

import pytest

from app.repositories.system_report_repo import SystemReportRepository
from app.services.system_report_service import SystemReportService


class FakeSystemReportRepository(SystemReportRepository):
    def __init__(self, session: Any = None) -> None:
        self.by_user = [
            {
                "user_id": "u-big",
                "email": "big@example.com",
                "document_count": 40,
                "storage_bytes": 900_000_000,
                "embedded_count": 38,
                "failed_count": 2,
            }
        ]
        self.totals = {
            "document_count": 120,
            "storage_bytes": 1_500_000_000,
            "pending_count": 3,
            "processing_count": 2,
            "embedded_count": 110,
            "failed_count": 5,
        }
        self.resources = {
            "total_users": 25,
            "suspended_users": 1,
            "total_courses": 80,
            "total_conversations": 300,
            "total_question_banks": 45,
            "active_jobs": 4,
            "failed_jobs": 6,
        }

    async def storage_by_user(self, limit: int = 100) -> list[dict[str, Any]]:
        return [dict(r) for r in self.by_user]

    async def storage_totals(self) -> dict[str, Any]:
        return dict(self.totals)

    async def resource_totals(self) -> dict[str, Any]:
        return dict(self.resources)


def make_service() -> tuple[SystemReportService, FakeSystemReportRepository]:
    repo = FakeSystemReportRepository()
    return SystemReportService(system_report_repo=repo), repo


@pytest.mark.asyncio
async def test_storage_report_has_totals_and_breakdown() -> None:
    service, _ = make_service()

    report = await service.storage_report()

    assert report["totals"]["document_count"] == 120
    assert int(report["totals"]["storage_bytes"]) == 1_500_000_000
    assert report["totals"]["embedded_count"] == 110
    assert report["by_user"][0]["email"] == "big@example.com"
    assert report["by_user"][0]["failed_count"] == "2"  # serialized as strings for JSON


@pytest.mark.asyncio
async def test_storage_status_accounting_covers_all_documents() -> None:
    """pending + processing + embedded + failed == total (report integrity)."""
    service, repo = make_service()

    totals = (await service.storage_report())["totals"]

    summed = (
        totals["pending_count"] + totals["processing_count"] + totals["embedded_count"] + totals["failed_count"]
    )
    assert summed == repo.totals["document_count"]


@pytest.mark.asyncio
async def test_resource_report_counts() -> None:
    service, _ = make_service()

    report = await service.resource_report()

    assert report["total_users"] == 25
    assert report["suspended_users"] == 1
    assert report["active_jobs"] == 4
    assert report["failed_jobs"] == 6
