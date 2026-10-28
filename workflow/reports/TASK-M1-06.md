# Task Completion Report: TASK-M1-06 — Login Attempt Limiting & Audit Security Logging (NFR04)

## 1. Status
**DONE** (backend-engineer role in main session)

## 2. Summary of Changes
- AuditRepository owning SQL for login_attempts (evidence) and audit_security_events (trail); identifier stored as SHA-256 hash (never plaintext email); details JSONB carries no secrets.
- Login flow integration: pre-check lockout (429 before password verification, correct passwords included); failures recorded with actor_user_id when the account exists (None otherwise); success records a succeeded attempt which resets the failure counter (count = failures since last success within window).
- Lockout anchored at the threshold-crossing attempt; rejections during lockout write nothing, so an attacker cannot extend the lockout indefinitely.
- Audit events: login_failed (warning, reason=invalid_credentials) per failure; login_locked (error) exactly once at threshold.
- Config: LOGIN_MAX_FAILED_ATTEMPTS=5, LOGIN_WINDOW_MINUTES=15, LOGIN_LOCKOUT_MINUTES=15 (+ .env.example) — proposed defaults pending PO approval (no document specifies numbers; Source-of-Truth Gap 8 marks storage mechanism as resolved by baseline tables).

## 3. Files Changed
- backend/app/repositories/audit_repo.py (new): failure state, record_attempt, record_security_event.
- backend/app/services/auth_service.py: optional AuditRepository, _enforce_login_rate_limit, _record_failed_login, success recording; TooManyAttemptsError.
- backend/app/api/v1/auth.py: 429 mapping. backend/app/core/config.py + backend/.env.example: settings.
- backend/tests/test_login_rate_limit.py (new): 10 tests.

## 4. Verification Commands & Real Results
- pytest tests/test_login_rate_limit.py -q: PASSED — 10 passed (threshold, lockout incl. correct-password 429, no window extension, reset-on-success, window expiry, hashed identifiers, no secrets in audit, per-identifier isolation, legacy compat).
- pytest -q: PASSED — 105 passed. ruff: PASSED. mypy: PASSED (28 files).

## 5. Unresolved Risks & Decisions
- Thresholds (5/15/15) are proposed defaults pending PO approval (no document defines numbers).
- IP-based throttling deferred (per-identifier only); no max-attempt enforcement on reset tokens (M1-04 note).
- Rate-limit SQL (window/count filters) untested against live PostgreSQL — integration verification pending Docker services.

## 6. Suggested Next Task
- TASK-M1-07: Social Login (Google OAuth2) Integration (US 2.1).
