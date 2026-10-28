# Task Completion Report: TASK-M4-01 — Central Gemini Adapter with Circuit Breaker & Retry (Invariant 4)

## 1. Status
**DONE**

## 2. Summary of Changes
- CircuitBreaker (providers): closed/open/half_open; threshold consecutive failed operations opens the circuit; cooldown elapses before one probe; success closes. Injectable clock for deterministic time tests.
- GeminiAdapter: the single Gemini SDK import point (Invariant 4). Lazy import + fail-closed ProviderNotConfiguredError; asyncio.wait_for timeout; up to 3 retries with exponential backoff (capped 8s); usage_metadata parsed and forwarded through an injectable on_usage hook (consumed by M4-03); model name always settings.gemini_model (never hard-coded); breaker counts failed operations, not attempts.
- build_prompt: untrusted data (retrieved chunks, user content) fenced inside a labeled BEGIN/END block with explicit "NOT an instruction" framing (Invariant 13 support).

## 3. Files Changed
- backend/app/providers/circuit_breaker.py, backend/app/providers/gemini_adapter.py (new), backend/tests/test_gemini_adapter.py (new, 12 tests).

## 4. Verification Commands & Real Results
- pytest tests/test_gemini_adapter.py -q: PASSED — 12 passed (breaker transitions incl. probe reopen, retry backoff sequence, open-circuit fast reject without provider call, timeout mapping, fail-closed without key, usage hook context, fenced prompt).
- pytest -q: PASSED — 217 passed (run twice, stable). ruff: PASSED. mypy: PASSED (73 files).

## 5. Unresolved Risks & Decisions
- google-genai SDK not in requirements (by design): adapter fails closed until the PO approves adding it with GEMINI_API_KEY. All tests use SDK-shaped fakes.
- Retry sleeps make the full suite ~29s (acceptable for CI; can be tuned via env if needed).

## 6. Suggested Next Task
- TASK-M4-02: Atomic Quota Reservation & Redis Fail-Closed Manager (US 2.21).
