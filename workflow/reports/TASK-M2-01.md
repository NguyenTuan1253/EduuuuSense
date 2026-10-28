# Task Completion Report: TASK-M2-01 — Courses CRUD (US 2.6)

## 1. Status
**DONE**

## 2. Summary of Changes
- Full courses CRUD. Ownership enforced in SQL (user_id in every statement); foreign owners get 404 (no existence oracle).
- Natural-key uniqueness (user_id, course_code, semester, academic_year) with PostgreSQL NULL-distinct semantics mirrored in the pre-check; conflicts -> 409 on create and on PATCH that alters the key.
- DELETE computes linked counts (assignments, exams, class_schedules, documents) inside the delete transaction and returns them — implements the requirement that deletion states its effect; cascade via FK.
- GET /courses defaults to active-only; ?status=archived|all widens (proposed default pending PO; archive concept exists in schema).
- Routers thin (schemas + wiring); SQL only in the repository; service orchestrates.

## 3. Files Changed
- backend/app/repositories/course_repo.py (new), backend/app/services/course_service.py (new), backend/app/api/v1/courses.py (new), backend/app/main.py (wiring), backend/tests/test_courses.py (new, 11 tests).

## 4. Verification Commands & Real Results
- pytest tests/test_courses.py -q: PASSED — 11 passed (ownership isolation cross-user 404, duplicate 409, per-owner uniqueness, filter defaults, delete counts, blank-name 422, unauthenticated 401).
- pytest -q: PASSED — 125 passed. ruff: PASSED. mypy: PASSED (36 files).

## 5. Unresolved Risks & Decisions
- List default active-only is a proposed UX default (no document specifies list behavior).
- Unit tests only (mocked repository); live-DB integration pending Docker services.

## 6. Suggested Next Task
- TASK-M2-02: Assignments Management (US 2.7 in backlog / US 2.7 SoT "Manage assignments").
