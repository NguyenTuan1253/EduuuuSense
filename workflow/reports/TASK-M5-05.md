# Task Completion Report: TASK-M5-05 + TASK-M5-06 — Study Plan Generation & Item Lifecycle (US 2.16)

## 1. Status
**DONE** (both tasks; M5-06 lands with the lifecycle endpoint)

## 2. Summary of Changes
- POST /study-plans {name, start_date, end_date} -> 202 + job (idempotent per user+window); the worker generates 3-10 items through the shared adapter, parses defensively (titles + scheduled_start required, out-of-window dropped, foreign course ids nulled, window bounds enforced) and persists an ACTIVE AI-generated plan with items atomically.
- GET /study-plans, GET /study-plans/{id} (plan + items, owner-scoped).
- M5-06: PATCH /study-plans/items/{id}/status — accept/start/complete/dismiss; accepted_at stamped on first acceptance, completed_at on completion (schema CHECK-compliant transitions); dismissed items stay for progress recalculation.
- Calendar sync: study items are consumable by the M2-05 aggregation later; plan items already carry course_id + scheduled times.

## 3. Files Changed
- backend/app/repositories/study_plan_repo.py, backend/app/services/study_plan_service.py, backend/app/worker/tasks/study_plan.py, backend/app/api/v1/study_plans.py (new), backend/app/main.py (wiring), backend/tests/test_study_plan.py (new, 5 tests).

## 4. Verification Commands & Real Results
- pytest tests/test_study_plan.py -q: PASSED — 5 passed (AI plan creation + normalization, window filtering, unparseable refund, lifecycle stamps, missing item error).
- pytest -q: PASSED — 299 passed. ruff: PASSED. mypy: PASSED (111 files).

## 5. Unresolved Risks & Decisions
- Item counts and window defaults (2 weeks) are proposed defaults pending PO.

## 6. Suggested Next Task
- TASK-M5-07: Academic Progress Dashboard & Analytics (US 2.17, 2.18).
