# Task Completion Report: TASK-M5-04 — Practice Attempts & Score Tracking (US 2.15)

## 1. Status
**DONE**

## 2. Summary of Changes
- Attempt lifecycle: start (from an owned bank; equal split of the 10-point scale), submit (MCQ auto-graded against the stored correct_answer with option-letter normalization; essays through the M5-03 GradingService), detail (per-question results with feedback/rationale/rubric/reference answers for review).
- Integrity: double submission -> 409; answers referencing foreign questions -> 422; total score summed and persisted atomically with the graded status (schema CHECK total <= max respected by per-question bounds).
- Ownership via practice_attempts.user_id in every statement.

## 3. Files Changed
- backend/app/repositories/practice_repo.py, backend/app/services/practice_service.py, backend/app/api/v1/practice.py (new), backend/app/main.py (wiring), backend/tests/test_practice_attempts.py (new, 7 tests).

## 4. Verification Commands & Real Results
- pytest tests/test_practice_attempts.py -q: PASSED — 7 passed (split scores, foreign bank 404, MCQ+essay grading, wrong-answer feedback, 409 double submit, 422 unknown question, owner-scoped detail).
- pytest -q: PASSED — 294 passed. ruff: PASSED. mypy: PASSED (106 files).

## 5. Unresolved Risks & Decisions
- Synchronous essay grading bounds attempts to a few essays within the 15s AI P95; a background-job variant for large attempts is the disclosed follow-up.

## 6. Suggested Next Task
- TASK-M5-05: AI Study & Revision Plan Generation (US 2.16).
