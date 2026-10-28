# Task Completion Report: TASK-M5-08 — Celery Beat Schedulers: Deadline & Exam Email Reminders (US 2.19)

## 1. Status
**DONE**

## 2. Summary of Changes
- Beat schedule (worker/scheduler.py): hourly deadline scans + 6-hourly exam scans (proposed defaults pending PO — no document specifies cadence). Beat enqueues through the broker; it never calls handlers directly.
- Scans: owners with open assignments due <=24h / exams starting <=72h (owner-scoped SQL); each item produces an in-app notification (Worker is an approved writer per the schema note on Invariant 12) with deduplication_key — same-day rescans are suppressed; email leg sends via stdlib SMTP off the event loop, marking the notification sent only on success; unconfigured SMTP skips the email leg gracefully (in-app still delivered); failed delivery keeps status pending for a later retry.
- SMTP settings (host/port/user/password/from) + .env.example; passwords never logged (recipient excluded from logs too).

## 3. Files Changed
- backend/app/repositories/notification_repo.py (new), backend/app/worker/tasks/email_tasks.py, backend/app/worker/scheduler.py (new), backend/app/core/config.py + backend/.env.example (SMTP), backend/tests/test_reminder_scheduler.py (new, 6 tests).

## 4. Verification Commands & Real Results
- pytest tests/test_reminder_scheduler.py -q: PASSED — 6 passed (creation + email, same-day dedup, 72h exam window, email-failure keeps pending, SMTP-unconfigured skip, broker-only beat entries).
- pytest -q: PASSED — 316 passed. ruff: PASSED. mypy: PASSED (120 files).

## 5. Unresolved Risks & Decisions
- Reminder windows (24h/72h) and scan cadence are proposed defaults pending PO; live SMTP delivery untested (no server configured).

## 6. Suggested Next Task
- TASK-M5-09: Notifications API & In-App Alerts (US 2.20, Invariant 12).
