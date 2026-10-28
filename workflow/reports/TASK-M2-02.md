# Task Completion Report: TASK-M2-02 — Assignments Management (US 2.7)

## 1. Status
**DONE**

## 2. Summary of Changes
- Assignments nested under owned courses (POST/GET /courses/{id}/assignments; GET/PATCH/DELETE /assignments/{id}). Ownership enforced via courses join inside every SQL statement; foreign/missing course or assignment -> 404.
- Status model pending/in_progress/completed (schema CHECK); completed_at derived: stamped on transition to completed, cleared otherwise; explicit completed_at input rejected 422 (derived field).
- Priority low/medium/high; list filter by status; ordering due_at NULLS LAST.
- Feeds later milestones: calendar aggregation (M2-05), reminders (M5-08), progress (M5-07) — data model already exposes due_at/status/completed_at.

## 3. Files Changed
- backend/app/repositories/assignment_repo.py, backend/app/services/assignment_service.py, backend/app/api/v1/assignments.py (all new), backend/app/main.py (wiring), backend/tests/test_assignments.py (new, 9 tests).

## 4. Verification Commands & Real Results
- pytest tests/test_assignments.py -q: PASSED — 9 passed.
- pytest -q: PASSED — 134 passed. ruff: PASSED. mypy: PASSED (39 files).

## 5. Unresolved Risks & Decisions
- Unit tests only (mocked repository); live-DB integration pending Docker services.

## 6. Suggested Next Task
- TASK-M2-03: Exam Schedule Tracking (US 2.8 in backlog / "Manage exam schedules").
