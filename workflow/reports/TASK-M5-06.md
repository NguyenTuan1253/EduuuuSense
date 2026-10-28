# Task Completion Report: TASK-M5-06 — Study Plan Item Acceptance, Dismissal & Calendar Sync

## 1. Status
**DONE** (delivered together with TASK-M5-05's pipeline; approved in M5-05's review)

## 2. Summary of Changes
- PATCH /api/v1/study-plans/items/{id}/status with whitelist statuses (pending/accepted/in_progress/completed/dismissed); accepted_at stamped on first acceptance and completed_at on completion per the schema CHECKs; owner-scoped; unknown items 404.
- Items carry course_id + scheduled times so the M2-05 calendar can aggregate them (sync path); course_progress (M5-07) recomputes from item states.

## 3. Files Changed
- Covered in TASK-M5-05's commit (study_plan_repo/service/api + tests test_study_plan.py).

## 4. Verification Commands & Real Results
- pytest tests/test_study_plan.py -q: PASSED — 5 passed (lifecycle stamps: acceptance, completion, dismissal; missing item 404).

## 5. Unresolved Risks & Decisions
- None beyond TASK-M5-05's notes.

## 6. Suggested Next Task
- TASK-M5-07: Academic Progress Dashboard & Analytics (US 2.17, 2.18).
