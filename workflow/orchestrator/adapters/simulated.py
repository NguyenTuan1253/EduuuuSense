"""Safe in-process simulation adapter for dummy end-to-end lifecycle verification.

Emulates both subagent phases without touching the real backlog, state file,
reports directory, git history, or review artifacts: the runner points this
adapter at a temporary workspace (temp backlog copy, temp state file, temp
reports directory) and the commit/PR path is disabled via EDUSENSE_NO_COMMIT.
"""

from pathlib import Path
from typing import Optional

from .base import ExecutionAdapter
from .shell import run_verification_commands
from ..models import TaskExecutionSpec, TaskExecutionResult

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


class SimulatedAdapter(ExecutionAdapter):
    """Controlled test adapter: dispatch succeeds immediately; ingest writes the
    artifact and simulated report, then re-runs declared verification commands."""

    def __init__(
        self,
        should_fail: bool = False,
        fail_on_attempt: Optional[int] = None,
        reports_dir: Optional[Path] = None,
    ) -> None:
        self.should_fail = should_fail
        self.fail_on_attempt = fail_on_attempt
        self.reports_dir = reports_dir or (REPO_ROOT / "workflow" / "reports")

    def is_available(self) -> tuple[bool, str]:
        return True, "Simulated adapter ready (controlled test mode)."

    def dispatch_task(self, spec: TaskExecutionSpec) -> TaskExecutionResult:
        if self.should_fail or (self.fail_on_attempt is not None and spec.attempt_number == self.fail_on_attempt):
            return TaskExecutionResult(
                success=False,
                task_id=spec.task_id,
                attempt_number=spec.attempt_number,
                error_message=f"Simulated dispatch failure on attempt {spec.attempt_number}",
            )
        return TaskExecutionResult(
            success=True,
            task_id=spec.task_id,
            attempt_number=spec.attempt_number,
            subagent="simulated",
            brief_path="<simulated>",
            agent_output=f"Simulated dispatch of {spec.task_id} to simulated subagent.",
        )

    def ingest_report(self, spec: TaskExecutionSpec) -> TaskExecutionResult:
        if self.should_fail or (self.fail_on_attempt is not None and spec.attempt_number == self.fail_on_attempt):
            return TaskExecutionResult(
                success=False,
                task_id=spec.task_id,
                attempt_number=spec.attempt_number,
                error_message=f"Simulated execution failure on attempt {spec.attempt_number}",
                agent_output="Task failed as commanded by simulation flag.",
            )

        # Simulate the subagent having produced its artifact and handoff report.
        for fpath in spec.affected_files:
            full_path = REPO_ROOT / fpath
            full_path.parent.mkdir(parents=True, exist_ok=True)
            content = (
                f"# Autonomous Task Execution Artifact\n"
                f"# Task: {spec.task_id}\n"
                f"# Attempt: {spec.attempt_number}\n"
                f"# Feedback: {spec.review_feedback or 'None'}\n"
            )
            full_path.write_text(content, encoding="utf-8")

        self.reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = self.reports_dir / f"{spec.task_id}.md"
        report_path.write_text(
            f"# Task Completion Report: {spec.task_id} — {spec.title}\n\n"
            f"## 1. Status\n**DONE** (simulated execution for lifecycle verification)\n\n"
            f"## 2. Summary of Changes\nSimulated completion of {spec.task_id}.\n\n"
            f"## 3. Files Changed\n" + "\n".join(f"- `{f}`: simulated artifact" for f in spec.affected_files) + "\n\n"
            f"## 4. Verification Commands & Real Results\n"
            + "\n".join(f"- `{c}`: re-executed below" for c in spec.verification_commands) + "\n\n"
            f"## 5. Unresolved Risks & Decisions\n- None (simulated)\n\n"
            f"## 6. Suggested Next Task\n- None (simulated)\n",
            encoding="utf-8",
        )

        ver_results, all_passed = run_verification_commands(spec.verification_commands)
        return TaskExecutionResult(
            success=all_passed,
            task_id=spec.task_id,
            attempt_number=spec.attempt_number,
            subagent="simulated",
            report_path=str(report_path),
            report_submitted=True,
            modified_files=spec.affected_files,
            verification_results=ver_results,
            error_message=None if all_passed else "Simulated verification failure.",
            agent_output=f"Task {spec.task_id} successfully executed in simulated mode.",
        )
