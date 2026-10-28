# Task Completion Report: TASK-M5-01 — Document Summarization Service (US 2.14)

## 1. Status
**DONE**

## 2. Summary of Changes
- Long AI work never runs in requests: POST /ai/documents/{id}/summary creates a background_jobs row (idempotency key summarization:{doc_id}) and enqueues the Celery task; the API returns 202 + job_id. GET /ai/jobs/{job_id} polls status/progress/result (owner-scoped 404 for foreign jobs).
- Worker: IdempotencyGuard (done jobs never re-run) -> plan-aware quota reserve -> summary generated from the document's embedded chunks assembled as a sanitized, fenced data block (marker-smuggling stripped; source capped at 60k chars) -> result persisted {summary, model_name, ai_generated: true}.
- Failure semantics: provider failure refunds quota and marks job failed/provider_error; quota exhausted marks failed/quota_exhausted (no refund); unavailable marks failed/quota_unavailable; documents not yet 'embedded' are rejected at the API with 422.

## 3. Files Changed
- backend/app/repositories/background_job_repo.py, backend/app/services/summary_service.py, backend/app/worker/tasks/ai.py, backend/app/api/v1/ai_jobs.py (new), backend/app/main.py (wiring), backend/tests/test_summarization.py (new, 8 tests).

## 4. Verification Commands & Real Results
- pytest tests/test_summarization.py -q: PASSED — 8 passed (202+queued, idempotent enqueue, 422 unembedded, owner-scoped poll, worker success persistence, provider failure refund, quota failure no-refund, fence integrity in the worker prompt).
- pytest -q: PASSED — 274 passed. ruff: PASSED. mypy: PASSED (95 files).

## 5. Unresolved Risks & Decisions
- Single-call bounded summarization (60k chars); map-reduce over larger docs is a follow-up needing a PO-approved chunk budget.

## 6. Suggested Next Task
- TASK-M5-02: Multiple-Choice & Essay Question Bank Generation (US 2.15).
