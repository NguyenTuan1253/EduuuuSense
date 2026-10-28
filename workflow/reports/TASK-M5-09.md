# Task Completion Report: TASK-M5-09 — Notifications API & In-App Alerts (US 2.20, Invariant 12)

## 1. Status
**DONE**

## 2. Summary of Changes
- GET /api/v1/notifications (all/unread filter, newest first, unread_count) and POST /{id}/read — the Notification Module ONLY reads/displays and marks read (Invariant 12); it has no write/create surface (structurally asserted in tests). Writers remain the Worker (M5-08 reminders), Progress, and the API Backend.
- Mark-read stamps read_at with is_read (schema CHECK respected); owner-scoped; foreign or already-read notifications return 404 (idempotency by 404, no information leak).

## 3. Files Changed
- backend/app/services/notification_service.py, backend/app/api/v1/notifications.py (new), backend/app/main.py (wiring), backend/tests/test_notifications.py (new, 4 tests).

## 4. Verification Commands & Real Results
- pytest tests/test_notifications.py -q: PASSED — 4 passed (list + unread count, unread filter, owner-scoped mark-read, read-only surface).
- pytest -q: PASSED — 320 passed. ruff: PASSED. mypy: PASSED (123 files).

## 5. Unresolved Risks & Decisions
- None.

## 6. Suggested Next Task
- TASK-M6-04: Admin Student Accounts Management (US 2.22) — M6-01..03 remain blocked by DECISION-PO-02.
