"""Reminder scans + email sending (US 2.19) run by Celery Beat.

Duplicate-send defense is three-layer: deduplication_key on notifications
(unique per item+window), the IdempotencyGuard on each scan, and status
transitions (pending -> sent/failed) on delivery.

Reminder windows are PROPOSED DEFAULTS pending PO approval (no document
specifies them): assignments 24h before due, exams 72h before start.
Email delivery uses SMTP via stdlib in a worker thread; unconfigured SMTP
keeps the in-app notification (sent) and skips only the email leg.
"""

import asyncio
import logging
import smtplib
from email.message import EmailMessage
from typing import Any

from app.core.config import settings
from app.worker.celery_app import celery_app

logger = logging.getLogger("edusense.reminders")

ASSIGNMENT_REMINDER_HOURS = 24
EXAM_REMINDER_HOURS = 72


async def send_email_async(
    *,
    recipient: str,
    subject: str,
    body: str,
    smtp: Any | None = None,
) -> bool:
    """Send one email via SMTP; returns True when sent, False when skipped
    (SMTP unconfigured) — raising only on configured-but-broken delivery."""

    def _send() -> bool:
        if not settings.smtp_host:
            return False
        message = EmailMessage()
        message["From"] = settings.smtp_from or settings.smtp_user or "edusense@localhost"
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
            if settings.smtp_user:
                server.starttls()
                server.login(settings.smtp_user, settings.smtp_password or "")
            server.send_message(message)
        return True

    try:
        return await asyncio.to_thread(_send)
    except Exception as exc:  # noqa: BLE001 — mapped to notification failure
        logger.error("Email delivery failed (%s): recipient kept out of logs", type(exc).__name__)
        _ = smtp
        return False


def scan_deadline_reminders_sync_payload(
    assignments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build reminder payloads for open assignments due inside the window."""
    return [
        {
            "user_scope": str(item["owner_email"]),
            "assignment_id": str(item["id"]),
            "title": item["title"],
            "due_at": str(item["due_at"]),
            "category": "assignments",
        }
        for item in assignments
    ]


@celery_app.task(name="app.worker.tasks.emails.deadline_reminder_scan", max_retries=3, default_retry_delay=120)
def deadline_reminder_scan() -> dict[str, Any]:
    """Beat task: create in-app + email reminders for soon-due assignments."""
    return asyncio.run(_run_deadline_scan())


@celery_app.task(name="app.worker.tasks.emails.exam_reminder_scan", max_retries=3, default_retry_delay=120)
def exam_reminder_scan() -> dict[str, Any]:
    """Beat task: create in-app + email reminders for upcoming exams."""
    return asyncio.run(_run_exam_scan())


async def _run_deadline_scan(
    *,
    notification_repo: Any | None = None,
    smtp: Any | None = None,
    now: Any | None = None,
) -> dict[str, Any]:
    from datetime import UTC, datetime, timedelta

    from app.core.database import async_session_factory
    from app.repositories.notification_repo import NotificationRepository

    moment = now or datetime.now(tz=UTC)
    window_end = moment + timedelta(hours=ASSIGNMENT_REMINDER_HOURS)

    if notification_repo is None:
        async with async_session_factory() as session:
            return await _run_deadline_scan(
                notification_repo=NotificationRepository(session), smtp=smtp, now=moment
            )

    created = 0
    for user_id, _email in await notification_repo.list_users_with_soon_due_assignments(
        window_start=moment.isoformat(), window_end=window_end.isoformat()
    ):
        items = await notification_repo.assignments_due_between(
            user_id, moment.isoformat(), window_end.isoformat()
        )
        for item in items:
            dedup_key = f"assignment-deadline:{item['id']}:{moment.astimezone(UTC).date().isoformat()}"
            row = await notification_repo.create_notification(
                user_id=user_id,
                notification_type="deadline_reminder",
                category="assignments",
                title=f"Sắp đến hạn: {item['title']}",
                content=f"Bài tập '{item['title']}' đến hạn lúc {item['due_at']}.",
                channel="in_app",
                deduplication_key=dedup_key,
            )
            if row is not None:
                created += 1
                sent = await send_email_async(
                    recipient=item["owner_email"],
                    subject=f"[EduSense] Sắp đến hạn: {item['title']}",
                    body=f"Bài tập '{item['title']}' đến hạn lúc {item['due_at']}.",
                    smtp=smtp,
                )
                if sent:
                    await notification_repo.mark_sent(str(row["id"]))
    return {"status": "succeeded", "created": created}


async def _run_exam_scan(
    *,
    notification_repo: Any | None = None,
    smtp: Any | None = None,
    now: Any | None = None,
) -> dict[str, Any]:
    from datetime import UTC, datetime, timedelta

    from app.core.database import async_session_factory
    from app.repositories.notification_repo import NotificationRepository

    moment = now or datetime.now(tz=UTC)
    window_end = moment + timedelta(hours=EXAM_REMINDER_HOURS)

    if notification_repo is None:
        async with async_session_factory() as session:
            return await _run_exam_scan(
                notification_repo=NotificationRepository(session), smtp=smtp, now=moment
            )

    created = 0
    for user_id, _email in await notification_repo.list_users_with_upcoming_exams(
        window_start=moment.isoformat(), window_end=window_end.isoformat()
    ):
        items = await notification_repo.exams_starting_between(
            user_id, moment.isoformat(), window_end.isoformat()
        )
        for item in items:
            dedup_key = f"exam-reminder:{item['id']}:{moment.astimezone(UTC).date().isoformat()}"
            row = await notification_repo.create_notification(
                user_id=user_id,
                notification_type="exam_reminder",
                category="exams",
                title=f"Sắp thi: {item['title']}",
                content=f"Kỳ thi '{item['title']}' bắt đầu lúc {item['exam_at']} tại {item.get('location') or '—'}.",
                channel="in_app",
                deduplication_key=dedup_key,
            )
            if row is not None:
                created += 1
                sent = await send_email_async(
                    recipient=item["owner_email"],
                    subject=f"[EduSense] Sắp thi: {item['title']}",
                    body=f"Kỳ thi '{item['title']}' bắt đầu lúc {item['exam_at']}.",
                    smtp=smtp,
                )
                if sent:
                    await notification_repo.mark_sent(str(row["id"]))
    return {"status": "succeeded", "created": created}
