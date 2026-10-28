# QA Review Dispatch: TASK-M5-03 — Subjective/Essay Answer Grading & Rubric Evaluation (US 2.15)

**Assigned subagent:** `backend-qa-reviewer` (invoke via the ZCode Agent tool with `subagent_type: "backend-qa-reviewer"`)
**Milestone:** M5 | **Attempt:** 1
**Branch:** `task/task-m5-03`

## Review objective
Independently review the implementation of TASK-M5-03 (Subjective/Essay Answer Grading & Rubric Evaluation (US 2.15)) against AGENTS.md,
`.agents/rules/`, the architecture invariants, and the task scope. You are the internal QA
gate before Product Owner approval; do not assume the implementing subagent's report is correct.

## Files in review scope
- `backend/app/services/grading_service.py`

## Declared verification commands (re-run them yourself; do not trust prior claims)
- `pytest backend/tests/test_grading_service.py -v`

## Required findings report
Write your findings to `workflow/reports/TASK-M5-03-qa-review.md`:
- Findings ordered by severity, citing file/line locations where possible.
- Distinguish confirmed defects from risks, assumptions, and missing tests.
- Explicitly list which checks you ran and which you did not.
- End with a verdict line: `**QA VERDICT:** PASS`, `PASS_WITH_NOTES`, or `FAIL`.

## Hard restrictions
- Read-only review: do not modify production implementation code.
- Run only safe local/test commands; never call external services.
