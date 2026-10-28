# Pull Request Review Artifact: [TASK-M2-03] Exam Schedule Tracking with Location & Dates (US 2.7)

**Branch:** `task/task-m2-03`  
**Base:** `main`  
**Remote GitHub PR:** Not opened (GITHUB_TOKEN not provided or remote PR creation skipped).

---

## Task Summary
- **Task ID:** TASK-M2-03
- **Milestone:** M2
- **Title:** Exam Schedule Tracking with Location & Dates (US 2.7)
- **Attempt:** 1
- **Implemented by subagent:** backend-engineer
- **Subagent report:** `workflow/reports/TASK-M2-03.md`

## Changed Files
- `backend/app/api/v1/exams.py`
- `backend/app/services/exam_service.py`
- `backend/app/repositories/exam_repo.py`

## Verification Results (re-executed by orchestrator)
- `pytest backend/tests/test_exams.py -v`: PASSED

## Commit
- Commit failed: On branch task/task-m2-03
Untracked files:
  (use "git add <file>..." to include in what will be committed)
	backlog.json
	workflow/

nothing added to commit but untracked files present (use "git add" to track)

QA review brief: `workflow/dispatches/TASK-M2-03-qa-review.md` (findings, when written, land in `workflow/reports/TASK-M2-03-qa-review.md`).
