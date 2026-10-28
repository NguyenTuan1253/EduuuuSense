# Pull Request Review Artifact: [TASK-M2-02] Assignments Management with Due Dates & Priority (US 2.6)

**Branch:** `task/task-m2-02`  
**Base:** `main`  
**Remote GitHub PR:** Not opened (GITHUB_TOKEN not provided or remote PR creation skipped).

---

## Task Summary
- **Task ID:** TASK-M2-02
- **Milestone:** M2
- **Title:** Assignments Management with Due Dates & Priority (US 2.6)
- **Attempt:** 1
- **Implemented by subagent:** backend-engineer
- **Subagent report:** `workflow/reports/TASK-M2-02.md`

## Changed Files
- `backend/app/api/v1/assignments.py`
- `backend/app/services/assignment_service.py`
- `backend/app/repositories/assignment_repo.py`

## Verification Results (re-executed by orchestrator)
- `pytest backend/tests/test_assignments.py -v`: PASSED

## Commit
- Commit failed: On branch task/task-m2-02
Untracked files:
  (use "git add <file>..." to include in what will be committed)
	backlog.json
	workflow/

nothing added to commit but untracked files present (use "git add" to track)

QA review brief: `workflow/dispatches/TASK-M2-02-qa-review.md` (findings, when written, land in `workflow/reports/TASK-M2-02-qa-review.md`).
