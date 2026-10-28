# Task Completion Report: TASK-M5-03 — Subjective/Essay Grading & Rubric Evaluation (US 2.15)

## 1. Status
**DONE**

## 2. Summary of Changes
- GradingService (REQUIRED core capability): grades essay answers via the shared adapter with strict-JSON output; all student/answer/reference content travels inside the fenced untrusted block (the reference answer itself is data, never instruction).
- Output validation: numeric score bounded to [0, max_score]; rubric breakdown entries bounds-checked and malformed entries dropped; missing/malformed JSON raises GradingOutputError (nothing invalid is ever persisted).
- Provider failures propagate for the caller (practice flow M5-04 maps them); usage logging hooks ride on the adapter.

## 3. Files Changed
- backend/app/services/grading_service.py (new), backend/tests/test_grading_service.py (new, 7 tests).

## 4. Verification Commands & Real Results
- pytest tests/test_grading_service.py -q: PASSED — 7 passed (validated breakdown, fenced reference, out-of-bounds score, malformed output, blank answer, rubric entry filtering, provider failure propagation).
- pytest -q: PASSED — 287 passed. ruff: PASSED. mypy: PASSED (102 files).

## 5. Unresolved Risks & Decisions
- Grading quality on Vietnamese essays is heuristic-model-dependent; a graded-sample golden set is recommended after live runs.

## 6. Suggested Next Task
- TASK-M5-04: Practice Attempts & Score Tracking API (US 2.15).
