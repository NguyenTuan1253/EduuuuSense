# Task Completion Report: TASK-M2-03 — Exam Schedule Tracking (US 2.8)

## 1. Status
**DONE**

## 2. Summary of Changes
- Exams nested under owned courses; ownership via courses join in every SQL statement; foreign/missing -> 404.
- Fields per schema: title (subject), exam_at (required timestamp), duration_minutes (1..1440), location, notes. Listing sorted soonest-first. Edit/delete per US 2.8.
- Feeds calendar aggregation (M2-05) and reminders (M5-08; ExamService.is_within_reminder_window helper added for the later scan).

## 3. Files Changed
- backend/app/repositories/exam_repo.py, backend/app/services/exam_service.py, backend/app/api/v1/exams.py (new), backend/app/main.py (wiring), backend/tests/test_exams.py (new, 7 tests).

## 4. Verification Commands & Real Results
- pytest tests/test_exams.py -q: PASSED — 7 passed.
- pytest -q: PASSED — 141 passed. ruff: PASSED. mypy: PASSED (44 files).

## 5. Unresolved Risks & Decisions
- Unit tests only (mocked repository); live-DB integration pending Docker services.

## 6. Suggested Next Task
- TASK-M2-04: Weekly Recurring Class Schedule Management.
