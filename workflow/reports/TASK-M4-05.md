# Task Completion Report: TASK-M4-05 — Permission-Scoped pgvector Vector Retrieval (Invariant 9)

## 1. Status
**DONE**

## 2. Summary of Changes
- RetrievalService: embeds the query through the shared adapter (Invariant 4) and retrieves via a single SQL statement whose ownership predicate is INSIDE the query: JOIN documents, d.user_id = :user_id, d.status='embedded' — never unscoped-then-filtered (Invariant 9).
- Optional course_id / document_ids filters are appended to the SQL (still server-side); top_k clamped to a configured maximum (default 10); blank queries rejected before spending an embedding call.
- Query vectors are always derived from query text server-side; client-supplied vectors are never accepted.

## 3. Files Changed
- backend/app/services/retrieval_service.py (new), backend/app/repositories/chunk_repo.py (fetch_similar_chunks), backend/tests/test_retrieval_service.py (new, 5 tests).

## 4. Verification Commands & Real Results
- pytest tests/test_retrieval_service.py -q: PASSED — 5 passed (SQL contains ownership predicate + embedded filter + LIMIT; subject scope passed; clamping; SQL-level filters; no client vectors).
- pytest -q: PASSED — 242 passed. ruff: PASSED. mypy: PASSED (85 files).

## 5. Unresolved Risks & Decisions
- ANN index tuning (HNSW/ivfflat, ef_search) is untested until a live pgvector runs — flagged for the docker compose integration pass.

## 6. Suggested Next Task
- TASK-M4-06: Prompt Injection Sanitization & Data Block Isolation (Invariant 13).
