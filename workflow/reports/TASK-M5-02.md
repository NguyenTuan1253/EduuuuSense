# Task Completion Report: TASK-M5-02 — Question Bank Generation (US 2.15)

## 1. Status
**DONE**

## 2. Summary of Changes
- POST /ai/documents/{id}/questions {count (1..20), question_type (multiple_choice|essay)} -> 202 + job_id (idempotency key per document+type); worker generates questions from embedded chunks through the shared adapter (fenced sanitized evidence, JSON-constrained output), parses defensively and persists bank+questions atomically (schema CHECKs mirrored: MCQ requires options array; difficulty whitelisted).
- GET /ai/question-banks (with counts), GET /ai/question-banks/{id}/questions (owner-scoped; MCQ correct answers included for the owner's practice flow).
- Failure semantics identical to M5-01 (provider refund, quota no-refund, invalid_model_output as an explicit failure with refund).

## 3. Files Changed
- backend/app/repositories/question_repo.py, backend/app/worker/tasks/questions.py, backend/app/api/v1/questions.py (new), backend/app/main.py (wiring), backend/tests/test_question_generation.py (new, 6 tests).

## 4. Verification Commands & Real Results
- pytest tests/test_question_generation.py -q: PASSED — 6 passed (MCQ bank persisted, essay no-options, unparseable refund, provider refund, quota no-refund, malformed-entry dropping).
- pytest -q: PASSED — 280 passed. ruff: PASSED. mypy: PASSED (99 files).

## 5. Unresolved Risks & Decisions
- Question quality/accuracy on Vietnamese content is measured by golden sets in later passes; count cap 20 is a proposed default.

## 6. Suggested Next Task
- TASK-M5-03: Subjective/Essay Answer Grading & Rubric Evaluation (US 2.15).
