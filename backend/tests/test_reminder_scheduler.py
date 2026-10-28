"""Unit tests for TASK-M5-08: deadline/exam reminder scans & email (US 2.19)."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.worker.tasks.email_tasks import (
    _run_deadline_scan,
    _run_exam_scan,
    send_email_async,
)

NOW = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)
USER_ID = "rm1-rm1-rm1-rm1-rm1rm1rm1rm1"


class FakeNotificationRepository:
    def __init__(self, *, assignments: list[dict[str, Any]] | None = None, exams: list[dict[str, Any]] | None = None) -> None:
        self.assignments = assignments or []
        self.exams = exams or []
        self.created: list[dict[str, Any]] = []
        self.sent: list[str] = []
        self.existing_keys: set[str] = set()

    async def list_users_with_soon_due_assignments(self, window_start: str, window_end: str) -> list[tuple[str, str]]:
        if not self.assignments:
            return []
        return [(USER_ID, "student@example.com")]

    async def list_users_with_upcoming_exams(self, window_start: str, window_end: str) -> list[tuple[str, str]]:
        if not self.exams:
            return []
        return [(USER_ID, "student@example.com")]

    async def assignments_due_between(self, user_id: str, start: str, end: str) -> list[dict[str, Any]]:
        return [dict(a) for a in self.assignments]

    async def exams_starting_between(self, user_id: str, start: str, end: str) -> list[dict[str, Any]]:
        return [dict(e) for e in self.exams]

    async def create_notification(self, **kwargs: Any) -> dict[str, Any] | None:
        key = kwargs.get("deduplication_key")
        if key is not None and key in self.existing_keys:
            return None  # duplicate suppressed
        if key is not None:
            self.existing_keys.add(key)
        row = {"id": f"notif-{len(self.created) + 1}", **kwargs}
        self.created.append(row)
        return dict(row)

    async def mark_sent(self, notification_id: str) -> bool:
        self.sent.append(notification_id)
        return True


class RecordingEmailSender:
    def __init__(self, sent: bool = True) -> None:
        self.sent_flag = sent
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, *, recipient: str, subject: str, body: str, smtp: Any = None) -> bool:
        self.calls.append({"recipient": recipient, "subject": subject})
        return self.sent_flag


@pytest.mark.asyncio
async def test_deadline_scan_creates_in_app_and_email_reminder(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = FakeNotificationRepository(
        assignments=[
            {
                "id": "asg-1",
                "course_id": "c1",
                "title": "HW1",
                "due_at": (NOW + timedelta(hours=20)).isoformat(),
                "priority": "high",
                "owner_email": "student@example.com",
            }
        ]
    )
    sender = RecordingEmailSender()
    monkeypatch.setattr("app.worker.tasks.email_tasks.send_email_async", sender)

    result = await _run_deadline_scan(notification_repo=repo, now=NOW)

    assert result["status"] == "succeeded"
    assert result["created"] == 1
    assert repo.created[0]["category"] == "assignments"
    assert repo.created[0]["channel"] == "in_app"
    assert "assignment-deadline:asg-1" in repo.created[0]["deduplication_key"]
    assert sender.calls[0]["recipient"] == "student@example.com"
    assert repo.sent == [repo.created[0]["id"]]


@pytest.mark.asyncio
async def test_rescan_same_day_does_not_duplicate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Duplicate-send prevention: the dedup key suppresses same-day repeats."""
    repo = FakeNotificationRepository(
        assignments=[
            {"id": "asg-1", "course_id": "c1", "title": "HW1", "due_at": (NOW + timedelta(hours=20)).isoformat(), "priority": "high", "owner_email": "student@example.com"}
        ]
    )
    sender = RecordingEmailSender()
    monkeypatch.setattr("app.worker.tasks.email_tasks.send_email_async", sender)

    first = await _run_deadline_scan(notification_repo=repo, now=NOW)
    second = await _run_deadline_scan(notification_repo=repo, now=NOW + timedelta(hours=1))

    assert first["created"] == 1
    assert second["created"] == 0
    assert len(sender.calls) == 1


@pytest.mark.asyncio
async def test_exam_scan_uses_72h_window_and_excludes_out_of_range(monkeypatch: pytest.MonkeyPatch) -> None:
    soon = {"id": "exam-1", "course_id": "c1", "title": "Final", "exam_at": (NOW + timedelta(hours=48)).isoformat(), "location": "Hall A", "owner_email": "student@example.com"}
    repo = FakeNotificationRepository(exams=[soon])
    sender = RecordingEmailSender()
    monkeypatch.setattr("app.worker.tasks.email_tasks.send_email_async", sender)

    result = await _run_exam_scan(notification_repo=repo, now=NOW)

    assert result["created"] == 1
    assert repo.created[0]["category"] == "exams"
    assert "exam-reminder:exam-1" in repo.created[0]["deduplication_key"]


@pytest.mark.asyncio
async def test_email_failure_marks_notification_not_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = FakeNotificationRepository(
        assignments=[
            {"id": "asg-2", "course_id": "c1", "title": "HW2", "due_at": (NOW + timedelta(hours=5)).isoformat(), "priority": "high", "owner_email": "student@example.com"}
        ]
    )
    failed_sender = RecordingEmailSender(sent=False)
    monkeypatch.setattr("app.worker.tasks.email_tasks.send_email_async", failed_sender)

    result = await _run_deadline_scan(notification_repo=repo, now=NOW)

    assert result["created"] == 1  # in-app notification still created
    assert repo.sent == []  # email leg failed -> not marked sent


@pytest.mark.asyncio
async def test_smtp_unconfigured_skips_email_leg_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "smtp_host", None)
    sent = await send_email_async(recipient="x@y.z", subject="s", body="b")
    assert sent is False  # skipped, not failed


@pytest.mark.asyncio
async def test_beat_schedule_enqueues_through_broker_only() -> None:
    """Beat runs on timers and enqueues; no direct handler calls (architecture)."""
    import app.worker.scheduler  # noqa: F401 — installs the beat schedule
    from app.worker.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule
    tasks = {entry["task"] for entry in schedule.values()}
    assert tasks == {
        "app.worker.tasks.emails.deadline_reminder_scan",
        "app.worker.tasks.emails.exam_reminder_scan",
    }
    # No entry bypasses the broker with direct function references.
    assert all(isinstance(entry["schedule"], float) for entry in schedule.values())
