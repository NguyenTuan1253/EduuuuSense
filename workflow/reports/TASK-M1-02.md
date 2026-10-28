# Task Completion Report: TASK-M1-02 — Email/Password Registration Endpoint (US 2.1)

## 1. Status
**DONE** (implemented by backend-engineer role in main session; subagent dispatch hit the plan quota limit, so the bounded assignment was executed directly under the same scope contract)

## 2. Summary of Changes
- Implemented the email/password registration vertical slice (US 2.1): `POST /api/v1/auth/register` creating a user, profile, seeded `student` role assignment, and a hashed single-use `email_verification` token in one atomic transaction.
- Email is normalized (trimmed, lowercased); passwords hashed with Argon2id via the restored `app/core/security.py` (TASK-M1-01). Only the SHA-256 token hash is persisted; the raw token is not returned (delivery via Email Sender/Celery is a later milestone).
- Fixed a latent M0 defect surfaced by the new validation path: the 422 envelope handler crashed because Pydantic v2 embeds raw `ValueError` objects in `exc.errors()`; it now serializes via `jsonable_encoder` (`backend/app/core/errors.py`, orchestrator-approved scope extension).
- Routers stay thin (schemas + wiring only); SQL is owned exclusively by the repository; no business logic outside the service.
- Note: this task was dispatched after correcting stale backlog statuses — the prior "completed" evidence for M1-02..05/07 referenced commits that never reached this repository (see recovery/task-m1-01 commit message and backlog evidence fields).

## 3. Files Changed
- `backend/app/repositories/user_repo.py`: new — SQL access for users/user_profiles/user_roles/verification_tokens (SQLAlchemy Core `text()`, atomic registration transaction; no ORM models exist in the project).
- `backend/app/services/auth_service.py`: new — registration use case: duplicate-email guard, Argon2id hashing, verification token staging; `DuplicateEmailError` carries no PII.
- `backend/app/api/v1/auth.py`: new — thin router with request/response schemas; boundary validation (email format via pragmatic regex — `email-validator` dependency intentionally not added; password policy; full_name length ≤ 150 matching `user_profiles.full_name`).
- `backend/app/api/v1/__init__.py`, `backend/app/services/__init__.py`, `backend/app/repositories/__init__.py`: new package inits.
- `backend/app/main.py`: wired `auth.router` under `settings.api_v1_prefix` (minimal include line, orchestrator-approved scope extension).
- `backend/app/core/errors.py`: one-line fix — `jsonable_encoder(exc.errors())` in the 422 handler (orchestrator-approved scope extension; latent M0 bug).
- `backend/tests/test_auth_register.py`: new — 19 tests covering success, Argon2id hashing, token staging, email normalization, case-insensitive duplicate rejection (409, no write), boundary validation matrix (10 invalid inputs), missing fields, policy floor acceptance, 500 on repository failure, and the debug-independent 500 envelope contract.

## 4. Verification Commands & Real Results
- `pytest tests/test_auth_register.py -q` (from `backend/`, venv interpreter): **PASSED — 19 passed**.
- `pytest -q` (full suite, from `backend/`): **PASSED — 65 passed** (46 baseline incl. 40 restored TASK-M1-01 security tests + 19 new; zero regressions).
- `ruff check .`: **PASSED — All checks passed!**
- `mypy .`: **PASSED — Success: no issues found in 22 source files.**
- Not run: integration test against live PostgreSQL (Docker services not running; per assignment, collaborators mocked at the repository boundary).

## 5. Unresolved Risks & Decisions
- Password-strength policy (min 8 chars, ≥1 letter, ≥1 digit) and email-format regex are proposed defaults — no document defines "password strength"; pending PO approval (suggest registering in `workflow/decisions.json`).
- Email-verification token TTL = 24h is a proposed default (US 2.3 defines 15 min only for password-reset codes); pending PO approval.
- Verification-email delivery is deferred (Email Sender/Celery worker belongs to later milestones); `verification_tokens` rows stage until then. Duplicate-email response uses generic envelope code `HTTP_409`; a dedicated `EMAIL_ALREADY_EXISTS` code would need an errors.py extension (follow-up).
- The main app runs with `debug=True` in development: Starlette bypasses the custom 500 handler and renders plaintext tracebacks by design; the 500 envelope contract is verified against a probe app with debug disabled (production posture).
- Unit tests only; live-DB integration verification remains open until Docker services are up.

## 6. Suggested Next Task
- **TASK-M1-03**: JWT Authentication, Login, Refresh Session & Revocation (US 2.2) — unblocks TASK-M1-04/05/06 and the login-attempt limiting work (NFR04).
