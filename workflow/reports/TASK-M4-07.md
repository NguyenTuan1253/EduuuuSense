# Task Completion Report: TASK-M4-07 — GroundingChecker & Citation Verification (Invariant 6, US 2.13)

## 1. Status
**DONE**

## 2. Summary of Changes
- GroundingChecker: every answer sentence must share lexical evidence with a retrieved chunk; coverage = supported/total sentences. Below the threshold (proposed default 0.5, pending PO) the checker refuses with the standard message ("No relevant information found in your documents for this question.") — answers are never passed through ungounded, and citations are never fabricated.
- Citations derive ONLY from provided chunks and map to the [n] positions given to the model; duplicates collapsed; chunk content is re-sanitized before matching (M4-06 integration) so injected text cannot inflate grounding.
- Heuristic is disclosed: lexical, deterministic, dependency-free; semantic similarity scoring would need a model decision (PO) and is measured by M4-08's golden set.

## 3. Files Changed
- backend/app/services/grounding_checker.py (new), backend/tests/test_grounding_checker.py (new, 7 tests).

## 4. Verification Commands & Real Results
- pytest tests/test_grounding_checker.py -q: PASSED — 7 passed (grounded pass + citation, unsupported refusal, no-evidence refusal, threshold semantics, multi-source citation mapping, injection-neutral matching, empty answer).
- pytest -q: PASSED — 255 passed. ruff: PASSED. mypy: PASSED (89 files).

## 5. Unresolved Risks & Decisions
- Coverage threshold 0.5 and lexical method are proposed defaults pending PO; the golden set (M4-08) quantifies accuracy and refusal quality.

## 6. Suggested Next Task
- TASK-M4-08: Golden Dataset RAG Evaluation & Accuracy Test Suite (Test Matrix 13.3).
