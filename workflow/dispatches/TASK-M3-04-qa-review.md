# QA Review Dispatch: TASK-M3-04 — Document Text Extraction (PDF/DOCX/Images) & Chunking Service

**Assigned subagent:** `backend-qa-reviewer` (invoke via the ZCode Agent tool with `subagent_type: "backend-qa-reviewer"`)
**Milestone:** M3 | **Attempt:** 1
**Branch:** `task/task-m3-04`

## Review objective
Independently review the implementation of TASK-M3-04 (Document Text Extraction (PDF/DOCX/Images) & Chunking Service) against AGENTS.md,
`.agents/rules/`, the architecture invariants, and the task scope. You are the internal QA
gate before Product Owner approval; do not assume the implementing subagent's report is correct.

## Files in review scope
- `backend/app/services/extraction_service.py`
- `backend/app/services/chunking_service.py`

## Declared verification commands (re-run them yourself; do not trust prior claims)
- `pytest backend/tests/test_text_extraction.py -v`

## Required findings report
Write your findings to `workflow/reports/TASK-M3-04-qa-review.md`:
- Findings ordered by severity, citing file/line locations where possible.
- Distinguish confirmed defects from risks, assumptions, and missing tests.
- Explicitly list which checks you ran and which you did not.
- End with a verdict line: `**QA VERDICT:** PASS`, `PASS_WITH_NOTES`, or `FAIL`.

## Hard restrictions
- Read-only review: do not modify production implementation code.
- Run only safe local/test commands; never call external services.
