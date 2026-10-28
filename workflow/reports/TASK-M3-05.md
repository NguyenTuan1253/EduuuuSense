# Task Completion Report: TASK-M3-05 — Embedding Generation & pgvector Storage (US 2.10)

## 1. Status
**DONE**

## 2. Summary of Changes
- EmbeddingAdapter (providers package — the only permitted Gemini import point, Invariant 4): lazy google-genai SDK import; fails closed with ProviderNotConfiguredError when the SDK or GEMINI_API_KEY is absent; every returned vector validated against settings.gemini_embedding_dimension (768 — matches the baseline vector(768) column; DECISION-PROP-01).
- ChunkRepository: retry-safe UPSERT on (document_id, chunk_index) in one transaction, pgvector literal with explicit cast, plus count/delete/owner-scoped fetch.
- Document status transitions (repository): atomic pending->processing claim; mark_embedded writes chunks_processed = total_chunks so 'embedded' is only valid when every chunk persisted (Invariant); mark_failed carries stable codes.
- ingest_document Celery task (documents queue) wrapped in IdempotencyGuard: done ingestions never re-run; extraction/empty failures mark the document failed (no retry storm); provider failures mark failed/provider_error; transient transport errors retry with backoff (max_retries=5). Persistence-boundary dimension guard as defense in depth.

## 3. Files Changed
- backend/app/providers/__init__.py, backend/app/providers/embedding_adapter.py (new), backend/app/repositories/chunk_repo.py (new), backend/app/repositories/document_repo.py (status transitions), backend/app/worker/tasks/documents.py (new), backend/tests/test_embedding_ingestion.py (new, 8 tests).

## 4. Verification Commands & Real Results
- pytest tests/test_embedding_ingestion.py -q: PASSED — 8 passed (happy path embedded with all chunks, extraction failure codes, provider failure, dimension mismatch fail-closed at both adapter and persistence boundary, guard skip after done, pgvector literal format, no-key fail-closed, wrong-dim client).
- pytest -q: PASSED — 198 passed. ruff: PASSED. mypy: PASSED (69 files).

## 5. Unresolved Risks & Decisions
- No live Gemini call (SDK not in requirements by design — provider fails closed until GEMINI_API_KEY + google-genai are provisioned; flagged for PO approval as a dependency decision).
- vector similarity search (ef_search tuning) lands with retrieval (M4-05); live pgvector insert untested until Docker services run.

## 6. Suggested Next Task
- TASK-M3-06: Document Search, Filtering & Tag Management API (US 2.11).
