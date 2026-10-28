# Task Completion Report: TASK-M5-07 — Academic Progress Dashboard & Analytics (US 2.17/2.18)

## 1. Status
**DONE**

## 2. Summary of Changes
- GET /dashboard/overview: active courses, deadlines within 7 days, overdue assignments, upcoming exams (7d), embedded documents — all owner-scoped aggregate SQL.
- GET /dashboard/progress: per-course progress recomputed from LIVE tables (assignments completed/total + committed study-plan items), weighted percent persisted via idempotent UPSERT into course_progress with last_calculated_at — the persisted snapshot can never silently drift.
- Risk classification (proposed default pending PO): 'overdue' when overdue assignments exist; 'at_risk' when completion < 50% with open work; else 'on_track'.

## 3. Files Changed
- backend/app/repositories/progress_repo.py, backend/app/services/progress_service.py, backend/app/api/v1/dashboard.py (new), backend/app/main.py (wiring), backend/tests/test_dashboard_analytics.py (new, 6 tests).

## 4. Verification Commands & Real Results
- pytest tests/test_dashboard_analytics.py -q: PASSED — 6 passed (weighted percent, overdue classification, at_risk threshold, overview shape, overdue flag, empty-course on_track).
- pytest -q: PASSED — 310 passed. ruff: PASSED. mypy: PASSED (116 files).

## 5. Unresolved Risks & Decisions
- Risk thresholds (50%) and the 7-day windows are proposed defaults pending PO.

## 6. Suggested Next Task
- TASK-M5-08: Celery Beat Schedulers — Deadline & Exam Email Reminders (US 2.19).
