"""Autonomous Task Orchestrator Runner binding the FSM, subagent dispatch, GitHub, and notifications.

Execution is a two-phase step machine driven by the persistent FSM state:

  - READY / CHANGES_REQUESTED / FAILED / DONE  ->  dispatch phase:
      validate blockers, open the task branch, route the task to its specialist
      subagent and emit the dispatch brief.
  - RUNNING  ->  ingest phase:
      read the subagent handoff report, re-run the declared verification
      commands locally, commit, produce the review artifact, and enter REVIEW.

The runner is a bookkeeper between subagent runs; it never claims task success
without locally re-executed verification evidence.
"""

import json
from pathlib import Path
from typing import Any, Optional

from .adapters.base import ExecutionAdapter
from .adapters.github import GitHubAdapter
from .adapters.notifier import Notifier
from .adapters.subagent import SubagentDispatchAdapter
from .models import EventType, TaskExecutionSpec, TaskState
from .state_machine import TaskStateMachine

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BACKLOG_PATH = REPO_ROOT / "workflow" / "backlog.json"
DECISIONS_PATH = REPO_ROOT / "workflow" / "decisions.json"


def _load_json(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: Path, data: dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


class OrchestratorRunner:
    """Coordinates the subagent task lifecycle: dispatch -> ingest -> review."""

    def __init__(
        self,
        backlog_path: Optional[Path] = None,
        decisions_path: Optional[Path] = None,
        state_machine: Optional[TaskStateMachine] = None,
        adapter: Optional[ExecutionAdapter] = None,
        github_adapter: Optional[GitHubAdapter] = None,
        notifier: Optional[Notifier] = None,
    ) -> None:
        self.backlog_path = backlog_path or BACKLOG_PATH
        self.decisions_path = decisions_path or DECISIONS_PATH
        self.fsm = state_machine or TaskStateMachine()
        self.adapter = adapter or SubagentDispatchAdapter()
        self.github = github_adapter or GitHubAdapter()
        self.notifier = notifier or Notifier()

    def select_next_eligible_task(self) -> Optional[dict[str, Any]]:
        """Identify next eligible pending task with all dependencies and approvals met."""
        backlog = _load_json(self.backlog_path)
        tasks = backlog.get("tasks", [])
        completed_ids = {t["id"] for t in tasks if t.get("status") == "completed"}

        for t in tasks:
            if t.get("status") == "pending":
                deps = t.get("dependencies", [])
                deps_met = all(d in completed_ids for d in deps)
                decisions_clear = not t.get("unresolved_decisions")
                if deps_met and decisions_clear:
                    return t
        return None

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def execute_next_task(
        self,
        target_task_id: Optional[str] = None,
        actor: str = "orchestrator",
        feedback: Optional[str] = None,
    ) -> dict[str, Any]:
        """Execute the single lifecycle step the FSM state calls for (dispatch or ingest)."""
        backlog = _load_json(self.backlog_path)
        tasks = backlog.get("tasks", [])

        # 1. Resolve task
        task_data = None
        if target_task_id:
            task_data = next((t for t in tasks if t["id"] == target_task_id), None)
            if not task_data:
                return {"status": "error", "message": f"Task '{target_task_id}' not found in backlog."}
        else:
            task_data = self.select_next_eligible_task()
            if not task_data:
                return {
                    "status": "idle",
                    "message": "No eligible pending tasks found. All remaining tasks may be blocked or completed.",
                }

        task_id = task_data["id"]

        # 2. Check blockers (unresolved PO decisions or pending approval requirement)
        if task_data.get("unresolved_decisions") or (task_data.get("requires_approval") and task_data.get("approval_status") != "approved"):
            self.fsm.process_event(
                EventType.ENCOUNTERED_BLOCKER,
                task_id=task_id,
                actor=actor,
                details={"reason": "Task blocked by unresolved PO decisions or approval requirement."},
            )
            msg = f"Task {task_id} is BLOCKED by pending approvals: {task_data.get('unresolved_decisions')}"
            self.notifier.notify(f"Task {task_id} Blocked", msg, level="WARN")
            return {"status": "blocked", "task_id": task_id, "message": msg}

        # 3. Route to the lifecycle phase demanded by the persistent FSM state
        state = self.fsm.state.current_state
        if state == TaskState.RUNNING and self.fsm.state.current_task_id == task_id:
            return self._ingest_phase(task_data, actor=actor)
        if state == TaskState.REVIEW and self.fsm.state.current_task_id == task_id:
            return {
                "status": "awaiting_review",
                "task_id": task_id,
                "message": (
                    f"Task {task_id} is in REVIEW. Submit a decision with: "
                    f"python workflow/workflow.py review --task-id {task_id} "
                    f"--decision APPROVED|CHANGES_REQUESTED --reviewer <PO_USER>"
                ),
            }

        return self._dispatch_phase(task_data, actor=actor, feedback=feedback)

    # ------------------------------------------------------------------
    # Phase 1: dispatch
    # ------------------------------------------------------------------

    def _dispatch_phase(self, task_data: dict[str, Any], actor: str, feedback: Optional[str] = None) -> dict[str, Any]:
        task_id = task_data["id"]

        # Reset stale state (e.g. FAILED, DONE, or RUNNING for a different task)
        if self.fsm.state.current_state not in (TaskState.READY, TaskState.CHANGES_REQUESTED) or (
            self.fsm.state.current_task_id not in (None, task_id)
        ):
            self.fsm.reset_for_next_task(task_id)

        if self.fsm.state.current_state != TaskState.RUNNING:
            self.fsm.process_event(EventType.START_TASK, task_id=task_id, actor=actor)

        # Prepare branch
        branch_ok, branch_name = self.github.ensure_task_branch(task_id)
        if not branch_ok:
            self.fsm.process_event(
                EventType.TASK_FAILED,
                task_id=task_id,
                actor=actor,
                details={"error": branch_name},
            )
            return {"status": "failed", "task_id": task_id, "message": f"Branch setup failed: {branch_name}"}
        self.fsm.state.branch_name = branch_name
        self.fsm.persist()

        # Build spec and route to the responsible subagent
        spec = TaskExecutionSpec(
            task_id=task_id,
            milestone=task_data.get("milestone", ""),
            title=task_data.get("title", ""),
            category=task_data.get("category", "backend"),
            dependencies=task_data.get("dependencies", []),
            affected_files=task_data.get("affected_files", []),
            verification_commands=task_data.get("verification_commands", []),
            review_feedback=feedback or self.fsm.state.review_feedback,
            attempt_number=self.fsm.state.attempt_number,
            branch_name=branch_name,
        )
        spec.subagent = self.adapter.route_task(spec) if isinstance(self.adapter, SubagentDispatchAdapter) else None

        self.notifier.notify(
            f"Dispatching {task_id}",
            f"Preparing dispatch brief for subagent '{spec.subagent or 'unrouted'}' "
            f"on branch '{branch_name}' (attempt {spec.attempt_number})...",
            level="INFO",
        )
        res = self.adapter.dispatch_task(spec)
        if not res.success:
            self.fsm.process_event(
                EventType.TASK_FAILED,
                task_id=task_id,
                actor=actor,
                details={"error": res.error_message or "Dispatch failed."},
            )
            return {
                "status": "failed",
                "task_id": task_id,
                "current_state": self.fsm.state.current_state,
                "error": res.error_message,
            }

        # Persist dispatch metadata for recovery across restarts
        self.fsm.state.assigned_subagent = res.subagent
        self.fsm.state.dispatch_path = res.brief_path
        self.fsm.state.report_path = None
        self.fsm.persist()

        self.notifier.notify(
            f"Task {task_id} Dispatched",
            (
                f"Dispatch brief: {res.brief_path}\n"
                f"Assigned subagent: {res.subagent}\n"
                f"Hand the brief to the subagent (ZCode Agent tool), then re-run:\n"
                f"  python workflow/workflow.py orchestrate\n"
                f"to ingest the completion report and verify evidence."
            ),
            level="INFO",
        )
        return {
            "status": "dispatched",
            "task_id": task_id,
            "subagent": res.subagent,
            "brief_path": res.brief_path,
            "branch": branch_name,
            "attempt": spec.attempt_number,
            "state": TaskState.RUNNING,
        }

    # ------------------------------------------------------------------
    # Phase 2: ingest
    # ------------------------------------------------------------------

    def _ingest_phase(self, task_data: dict[str, Any], actor: str) -> dict[str, Any]:
        task_id = task_data["id"]

        spec = TaskExecutionSpec(
            task_id=task_id,
            milestone=task_data.get("milestone", ""),
            title=task_data.get("title", ""),
            category=task_data.get("category", "backend"),
            dependencies=task_data.get("dependencies", []),
            affected_files=task_data.get("affected_files", []),
            verification_commands=task_data.get("verification_commands", []),
            review_feedback=self.fsm.state.review_feedback,
            attempt_number=self.fsm.state.attempt_number,
            subagent=self.fsm.state.assigned_subagent,
            branch_name=self.fsm.state.branch_name,
        )

        res = self.adapter.ingest_report(spec)
        if not res.success:
            # No report on disk yet: the subagent is still working (or was never
            # handed the brief). Not a failed attempt — stay RUNNING and wait.
            if not res.report_submitted:
                return {
                    "status": "waiting_for_report",
                    "task_id": task_id,
                    "state": TaskState.RUNNING,
                    "message": res.error_message,
                }
            err_msg = res.error_message or "Task verification failed."
            new_state, _ = self.fsm.process_event(
                EventType.TASK_FAILED,
                task_id=task_id,
                actor=actor,
                details={"error": err_msg, "attempt": spec.attempt_number},
            )
            self.notifier.notify(
                f"Task {task_id} Failed",
                f"Task ingest failed on attempt {spec.attempt_number}. Current state: {new_state}.\nError: {err_msg}",
                level="ERROR",
            )
            return {
                "status": "failed",
                "task_id": task_id,
                "current_state": new_state,
                "error": err_msg,
            }

        # Commit declared files and prepare PR / review artifact
        commit_ok, commit_ref = self.github.stage_and_commit(task_id, spec.title, spec.affected_files)
        report_ref = res.report_path or "N/A"
        pr_body = (
            f"## Task Summary\n"
            f"- **Task ID:** {task_id}\n"
            f"- **Milestone:** {spec.milestone}\n"
            f"- **Title:** {spec.title}\n"
            f"- **Attempt:** {spec.attempt_number}\n"
            f"- **Implemented by subagent:** {res.subagent or spec.subagent or 'N/A'}\n"
            f"- **Subagent report:** `{report_ref}`\n\n"
            f"## Changed Files\n"
            + "\n".join(f"- `{f}`" for f in spec.affected_files)
            + f"\n\n## Verification Results (re-executed by orchestrator)\n"
            + "\n".join(f"- `{v.command}`: {'PASSED' if v.passed else 'FAILED'}" for v in res.verification_results)
            + f"\n\n## Commit\n- {commit_ref}\n"
            f"\nQA review brief: `workflow/dispatches/{task_id}-qa-review.md` "
            f"(findings, when written, land in `workflow/reports/{task_id}-qa-review.md`)."
        )
        pr_ok, pr_msg, pr_ref = self.github.create_or_update_pr(task_id, spec.title, pr_body)

        # Emit the QA review brief for backend-qa-reviewer (internal gate, human approval unchanged)
        qa_brief_path: Optional[str] = None
        if isinstance(self.adapter, SubagentDispatchAdapter):
            qa_res = self.adapter.dispatch_qa_review(spec, changed_files=spec.affected_files)
            qa_brief_path = qa_res.brief_path

        # Transition RUNNING -> REVIEW
        self.fsm.process_event(
            EventType.TASK_COMPLETED_SUCCESS,
            task_id=task_id,
            actor=actor,
            details={
                "branch_name": spec.branch_name or self.fsm.state.branch_name,
                "pr_url": pr_ref,
                "commit": commit_ref,
                "subagent": res.subagent,
                "report_path": res.report_path,
            },
        )

        self.notifier.notify(
            f"Task {task_id} Ready for Review",
            (
                f"Task {task_id} ({spec.title}) ingested and verification re-executed successfully.\n"
                f"Branch: {spec.branch_name or self.fsm.state.branch_name}\n"
                f"Subagent report: {report_ref}\n"
                f"QA review brief: {qa_brief_path or 'skipped (non-dispatch adapter)'}\n"
                f"Review artifact: {pr_ref or pr_msg}\n"
                f"Current state: REVIEW (awaiting explicit Product Owner decision).\n"
                f"To approve: python workflow/workflow.py review --task-id {task_id} --decision APPROVED --reviewer <PO_USER>\n"
                f"To request changes: python workflow/workflow.py review --task-id {task_id} --decision CHANGES_REQUESTED --feedback '<NOTES>'"
            ),
            level="INFO",
        )

        return {
            "status": "review_ready",
            "task_id": task_id,
            "subagent": res.subagent,
            "report_path": res.report_path,
            "qa_brief_path": qa_brief_path,
            "branch": spec.branch_name or self.fsm.state.branch_name,
            "pr": pr_ref,
            "state": TaskState.REVIEW,
        }

    # ------------------------------------------------------------------
    # Review decisions
    # ------------------------------------------------------------------

    def process_review_decision(
        self,
        task_id: str,
        reviewer: str,
        decision: str,
        feedback: Optional[str] = None,
    ) -> dict[str, Any]:
        """Process explicit human review decision (APPROVED or CHANGES_REQUESTED)."""
        if self.fsm.state.current_task_id != task_id:
            return {"status": "error", "message": f"Active task in state machine is '{self.fsm.state.current_task_id}', not '{task_id}'."}

        if self.fsm.state.current_state != TaskState.REVIEW:
            return {"status": "error", "message": f"Task '{task_id}' is in state '{self.fsm.state.current_state}', not REVIEW."}

        # 1. Verify reviewer identity and permissions
        auth_ok, auth_msg = self.github.verify_approver(reviewer)
        if not auth_ok:
            return {"status": "error", "message": f"Permission denied: {auth_msg}"}

        dec_upper = decision.upper().strip()

        # 2. Handle CHANGES_REQUESTED
        if dec_upper in ("CHANGES_REQUESTED", "REJECT", "REVISE"):
            notes = feedback or "Reviewer requested changes."
            self.fsm.process_event(
                EventType.REQUEST_CHANGES,
                task_id=task_id,
                actor=reviewer,
                details={"feedback": notes},
            )
            self.notifier.notify(
                f"Changes Requested on {task_id}",
                f"Reviewer '{reviewer}' requested changes on task {task_id}.\nFeedback: {notes}\n"
                f"State transitioned to CHANGES_REQUESTED. Re-dispatch to the subagent with:\n"
                f"  python workflow/workflow.py orchestrate --resume --feedback '<NOTES>'",
                level="WARN",
            )
            return {"status": "changes_requested", "task_id": task_id, "feedback": notes}

        # 3. Handle APPROVED
        if dec_upper == "APPROVED":
            # Check required CI status
            ci_ok, ci_msg = self.github.check_ci_status()
            if not ci_ok:
                return {
                    "status": "error",
                    "message": f"Cannot approve task: Required CI verification checks did not pass:\n{ci_msg}",
                }

            # Transition REVIEW -> APPROVED -> DONE
            self.fsm.process_event(
                EventType.APPROVE_TASK,
                task_id=task_id,
                actor=reviewer,
                details={"approver": reviewer, "ci_status": ci_msg},
            )
            self.fsm.process_event(EventType.FINALIZE_TASK, task_id=task_id, actor=reviewer)

            # Update canonical backlog.json
            backlog = _load_json(self.backlog_path)
            for t in backlog.get("tasks", []):
                if t["id"] == task_id:
                    t["status"] = "completed"
                    t["evidence"] = f"Approved by {reviewer} with passing CI verification ({ci_msg})"
                    break
            _save_json(self.backlog_path, backlog)

            self.notifier.notify(
                f"Task {task_id} Approved & Completed",
                f"Task {task_id} approved by {reviewer}. CI verified. Status recorded as DONE in canonical backlog.",
                level="INFO",
            )

            # Check if all tasks in M0.5-M6 are done
            completion = self.check_project_completion()
            if completion["is_complete"]:
                self.notifier.notify("Project Milestones Completed!", completion["report"], level="INFO")

            return {
                "status": "completed",
                "task_id": task_id,
                "approved_by": reviewer,
                "project_completion": completion["is_complete"],
            }

        return {"status": "error", "message": f"Unsupported decision '{decision}'. Choose 'APPROVED' or 'CHANGES_REQUESTED'."}

    def check_project_completion(self) -> dict[str, Any]:
        """Detect when every required task in milestones M0.5–M6 is completed and generate completion evidence."""
        backlog = _load_json(self.backlog_path)
        tasks = backlog.get("tasks", [])
        m_tasks = [t for t in tasks if t.get("milestone") in ("M0.5", "M1", "M2", "M3", "M4", "M5", "M6")]

        completed = [t for t in m_tasks if t.get("status") == "completed"]
        is_complete = len(completed) == len(m_tasks) and len(m_tasks) > 0

        report = f"# Project Completion Report (Milestones M0.5–M6)\n\n"
        report += f"- Total Scope Tasks: {len(m_tasks)}\n"
        report += f"- Completed Tasks: {len(completed)}\n"
        report += f"- Overall Milestone Completion: {len(completed)}/{len(m_tasks)} ({len(completed)/len(m_tasks)*100:.1f}%)\n"

        if is_complete:
            report += "\nAll required milestones M0.5 through M6 are fully completed with passing verification suites.\n"

        return {"is_complete": is_complete, "report": report}
