# QA Review Dispatch: TASK-M1-05 — User Profile CRUD & Student Information (US 2.4)

**Assigned subagent:** `backend-qa-reviewer` (invoke via the ZCode Agent tool with `subagent_type: "backend-qa-reviewer"`)
**Milestone:** M1 | **Attempt:** 1
**Branch:** `task/task-m1-05`

## Review objective
Independently review the implementation of TASK-M1-05 (User Profile CRUD & Student Information (US 2.4)) against AGENTS.md,
`.agents/rules/`, the architecture invariants, and the task scope. You are the internal QA
gate before Product Owner approval; do not assume the implementing subagent's report is correct.

## Files in review scope
- `backend/app/api/v1/users.py`
- `backend/app/services/user_service.py`
- `backend/app/repositories/user_repo.py`

## Declared verification commands (re-run them yourself; do not trust prior claims)
- `pytest backend/tests/test_user_profile.py -v`

## Required findings report
Write your findings to `workflow/reports/TASK-M1-05-qa-review.md`:
- Findings ordered by severity, citing file/line locations where possible.
- Distinguish confirmed defects from risks, assumptions, and missing tests.
- Explicitly list which checks you ran and which you did not.
- End with a verdict line: `**QA VERDICT:** PASS`, `PASS_WITH_NOTES`, or `FAIL`.

## Hard restrictions
- Read-only review: do not modify production implementation code.
- Run only safe local/test commands; never call external services.
