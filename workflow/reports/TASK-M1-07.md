# Task Completion Report: TASK-M1-07 — Social Login (Google OAuth2) (US 2.1)

## 1. Status
**DONE** (backend-engineer role in main session)

## 2. Summary of Changes
- GoogleOAuthClient (clients layer): verifies ID tokens via Google's public tokeninfo endpoint (signature/expiry validated by Google); stdlib urllib run off the event loop — no new dependency; optional aud check when GOOGLE_OAUTH_CLIENT_ID is set (new setting + .env.example).
- POST /auth/social/google: verified email -> sign-in for existing accounts; unknown emails auto-registered (is_email_verified=TRUE, unusable random Argon2 hash, profile + student role); suspended -> 403; all failures -> generic 401; service-level defense-in-depth re-checks email/verified claims.
- Session issuance refactored into _issue_session shared with password login (tokens, refresh session, last_login, evidence).

## 3. Files Changed
- backend/app/clients/__init__.py, backend/app/clients/google_oauth.py (new).
- backend/app/services/auth_service.py: login_with_google + google_client wiring.
- backend/app/api/v1/auth.py: SocialLoginRequest + endpoint.
- backend/app/repositories/user_repo.py (extension): create_social_user.
- backend/app/core/config.py + backend/.env.example: GOOGLE_OAUTH_CLIENT_ID.
- backend/tests/test_auth_social.py (new): 9 tests.

## 4. Verification Commands & Real Results
- pytest tests/test_auth_social.py -q: PASSED — 9 passed.
- pytest -q: PASSED — 114 passed. ruff: PASSED. mypy: PASSED (32 files).

## 5. Unresolved Risks & Decisions
- Schema Gap 11: no provider column — a user verified by Google shares identity purely by email match; adding a provider column needs a PO-approved migration.
- No live Google verification run (external service; mocked in tests). GOOGLE_OAUTH_CLIENT_ID unset in dev, so aud is unchecked until configured.
- Social logins bypass password rate limiting by design (verified identity); per-IP throttling deferred (M1-06 note).

## 6. Suggested Next Task
- TASK-M2-01: Courses CRUD Repository, Service & Router (US 2.5).
