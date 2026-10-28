# Task Completion Report: TASK-M3-03 — Celery Worker Setup & Task Idempotency Guard (Invariant 8)

## 1. Status
**DONE**

## 2. Summary of Changes
- Celery application: Redis broker/backend from settings, JSON serialization, UTC, acks_late with prefetch=1 (safe retries), and per-family queues (edusense_documents / edusense_ai / edusense_emails) wired for the later task milestones. Beat is NOT configured here (M5-08 scope).
- IdempotencyGuard on the task_idempotency table: run_guarded(repo, key, task_type, work) — fresh key acquired atomically (ON CONFLICT DO NOTHING), already-in-progress skipped (reason in_progress), done keys never re-executed, failed keys re-acquired for retry; work exceptions mark failed and re-raise for Celery retry; done is written ONLY after success.
- requirements.txt: added celery[redis]==5.5.3 — the stack already mandates Celery (AGENTS.md section 2, DECISION-CONFIRMED-02); the pin was missing.

## 3. Files Changed
- backend/app/worker/celery_app.py, backend/app/worker/idempotency.py, backend/app/worker/__init__.py, backend/app/worker/tasks/__init__.py (new), backend/requirements.txt (pin added), backend/tests/test_worker_idempotency.py (new, 7 tests).

## 4. Verification Commands & Real Results
- pytest tests/test_worker_idempotency.py -q: PASSED — 7 passed (first-run completion, no re-execution after done, concurrent in_progress blocked, failed->retry->done, partial failure not marked done, celery wiring, outcome shape).
- pytest -q: PASSED — 182 passed. ruff: PASSED. mypy: PASSED (61 files).

## 5. Unresolved Risks & Decisions
- No live Redis/Celery worker run (services down); broker behavior verified only as configuration. E2E queue verification pending docker compose up.

## 6. Suggested Next Task
- TASK-M3-04: Document Text Extraction & Chunking Service.
