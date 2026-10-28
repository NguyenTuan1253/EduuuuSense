# Task Completion Report: TASK-M1-05 — User Profile CRUD (US 2.4)

## 1. Status
**DONE** (backend-engineer role in main session)

## 2. Summary of Changes
- get_current_user_id: shared Bearer JWT dependency (service layer) gating every protected endpoint; subject taken only from the signed token; 401 with WWW-Authenticate.
- GET /users/me: account + profile (owner-only by token subject).
- PUT /users/me/profile: PATCH-style — only explicitly provided fields applied; whitelist (full_name, display_name, avatar_url, phone_number, university, major, academic_year); academic_year 1..10 mirrors the schema CHECK; blank values rejected; UPSERT profile row.
- errors.py: http_exception_handler now forwards headers (WWW-Authenticate was silently dropped by the M0 envelope — latent bug fix, orchestrator-approved scope extension).
- Email change (US 2.4 mentions re-verification on email change) DEFERRED: schema has no pending-email storage; needs a migration decision from the PO. Registered as unresolved.

## 3. Files Changed
- backend/app/services/user_service.py (new): UserService + get_current_user_id.
- backend/app/api/v1/users.py (new): schemas + 2 endpoints.
- backend/app/repositories/user_repo.py (extension): get_user_with_profile, update_user_profile (UPSERT, whitelist).
- backend/app/main.py: router wiring. backend/app/core/errors.py: header forwarding.
- backend/tests/test_user_profile.py (new): 9 tests.

## 4. Verification Commands & Real Results
- pytest tests/test_user_profile.py -q: PASSED — 9 passed (401 without token, refresh-token-as-access rejected, owner scoping, boundary validation, empty/blank rejection).
- pytest -q: PASSED — 95 passed. ruff: PASSED. mypy: PASSED (27 files).

## 5. Unresolved Risks & Decisions
- Email change flow deferred (no pending_email column; migration requires PO approval).
- Unit tests only; live-DB integration pending Docker services.

## 6. Suggested Next Task
- TASK-M1-06: Login Attempt Limiting & Audit Security Logging (NFR04).
