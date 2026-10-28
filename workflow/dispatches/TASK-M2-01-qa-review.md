# QA Review Dispatch: TASK-M2-01 — Courses CRUD Repository, Service & Router (US 2.5)

**Assigned subagent:** `backend-qa-reviewer` (invoke via the ZCode Agent tool with `subagent_type: "backend-qa-reviewer"`)
**Milestone:** M2 | **Attempt:** 1
**Branch:** `task/task-m2-01`

## Review objective
Independently review the implementation of TASK-M2-01 (Courses CRUD Repository, Service & Router (US 2.5)) against AGENTS.md,
`.agents/rules/`, the architecture invariants, and the task scope. You are the internal QA
gate before Product Owner approval; do not assume the implementing subagent's report is correct.

## Files in review scope
- `backend/app/api/v1/courses.py`
- `backend/app/services/course_service.py`
- `backend/app/repositories/course_repo.py`

## Declared verification commands (re-run them yourself; do not trust prior claims)
- `pytest backend/tests/test_courses.py -v`

## Required findings report
Write your findings to `workflow/reports/TASK-M2-01-qa-review.md`:
- Findings ordered by severity, citing file/line locations where possible.
- Distinguish confirmed defects from risks, assumptions, and missing tests.
- Explicitly list which checks you ran and which you did not.
- End with a verdict line: `**QA VERDICT:** PASS`, `PASS_WITH_NOTES`, or `FAIL`.

## Hard restrictions
- Read-only review: do not modify production implementation code.
- Run only safe local/test commands; never call external services.
