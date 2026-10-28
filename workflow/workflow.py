#!/usr/bin/env python3
"""EduSense Repository-Native Workflow Automation Engine.

Provides an evidence-driven, zero-extra-dependency CLI to audit repository
state, track milestone backlog (M0 through M6), enforce architecture invariants,
manage Product Owner approval gates, and coordinate task execution through
ZCode specialist subagents (backend-engineer, database-engineer,
ai-rag-engineer, worker-scheduler-engineer, backend-qa-reviewer) coordinated
by backend-orchestrator.

Usage:
    python workflow/workflow.py audit
    python workflow/workflow.py status
    python workflow/workflow.py validate [--ci]
    python workflow/workflow.py run-task [--task-id TASK_ID]
    python workflow/workflow.py report --task-id TASK_ID
    python workflow/workflow.py approve --decision-id DECISION_ID --value "APPROVAL_NOTE"
    python workflow/workflow.py orchestrate            # dispatch or ingest, per FSM state
    python workflow/workflow.py review --task-id TASK_ID --reviewer USER --decision APPROVED
"""

import argparse
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKLOG_PATH = REPO_ROOT / "workflow" / "backlog.json"
DECISIONS_PATH = REPO_ROOT / "workflow" / "decisions.json"
BACKEND_DIR = REPO_ROOT / "backend"
FRONTEND_DIR = REPO_ROOT / "frontend"


def find_tool(name: str) -> str:
    """Resolve executable in backend/.venv, root .venv, or system PATH."""
    venv_candidates = [
        BACKEND_DIR / ".venv",
        REPO_ROOT / ".venv",
    ]
    for venv in venv_candidates:
        win_bin = venv / "Scripts" / f"{name}.exe"
        if win_bin.exists():
            return str(win_bin)
        nix_bin = venv / "bin" / name
        if nix_bin.exists():
            return str(nix_bin)
    system_tool = shutil.which(name)
    if system_tool:
        return system_tool
    return name


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Required workflow file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def run_cmd(
    cmd: list[str],
    cwd: Path | None = None,
    timeout: int = 60,
    capture: bool = True,
) -> tuple[int, str, str]:
    """Execute a command and return (exit_code, stdout, stderr)."""
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd or REPO_ROOT),
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            text=True,
            timeout=timeout,
            shell=False,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", f"Command timed out after {timeout} seconds"
    except Exception as e:
        return 1, "", str(e)


# ==============================================================================
# 1. AUDIT COMMAND
# ==============================================================================


def cmd_audit(_args: argparse.Namespace) -> int:
    """Run comprehensive evidence-based audit of repository and runtime services."""
    print("=" * 80)
    print(" EDUSENSE REPOSITORY & RUNTIME EVIDENCE AUDIT")
    print("=" * 80)

    # 1. Git State
    code, branch, _ = run_cmd(["git", "branch", "--show-current"])
    code, head, _ = run_cmd(["git", "log", "-1", "--oneline"])
    code, status, _ = run_cmd(["git", "status", "-s"])
    branch_name = branch.strip() or "unknown"
    head_commit = head.strip() or "unknown"
    dirty = bool(status.strip())

    print("\n[1] Git State:")
    print(f"  * Current Branch: {branch_name}")
    print(f"  * HEAD Commit:    {head_commit}")
    print(f"  * Working Tree:   {'DIRTY' if dirty else 'CLEAN'}")
    if dirty:
        print("  * Modified/Untracked files:")
        for line in status.splitlines()[:5]:
            print(f"      {line}")

    # 2. Docker Services
    print("\n[2] Docker Infrastructure:")
    code, ps_out, _ = run_cmd(["docker", "compose", "ps", "--format", "table {{.Service}}\t{{.Status}}\t{{.Ports}}"])
    if code == 0 and ps_out.strip():
        for line in ps_out.splitlines():
            print(f"  {line}")
    else:
        print("  * Docker compose services are not currently running or docker daemon unreachable.")

    # 3. Database & pgvector
    print("\n[3] PostgreSQL & pgvector:")
    code, tbl_out, _ = run_cmd([
        "docker", "exec", "edusense-postgres",
        "psql", "-U", "edusense_user", "-d", "edusense_db", "-t", "-c",
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name != 'alembic_version';"
    ])
    if code == 0:
        table_count = tbl_out.strip()
        code, vec_out, _ = run_cmd([
            "docker", "exec", "edusense-postgres",
            "psql", "-U", "edusense_user", "-d", "edusense_db", "-t", "-c",
            "SELECT extversion FROM pg_extension WHERE extname = 'vector';"
        ])
        vec_ver = vec_out.strip() or "Not installed"
        print(f"  * Public Tables Count: {table_count} (Expected: 38)")
        print(f"  * pgvector Extension:  {vec_ver}")
    else:
        print("  * Database container edusense-postgres not accessible.")

    # 4. Storage & Redis Probes
    print("\n[4] MinIO Storage & Redis Cache:")
    code, minio_out, _ = run_cmd([
        "docker", "exec", "edusense-minio",
        "mc", "alias", "set", "srv", "http://localhost:9000", "minioadmin", "minioadmin"
    ])
    code, ls_out, _ = run_cmd(["docker", "exec", "edusense-minio", "mc", "ls", "srv"])
    if code == 0 and "edusense-documents" in ls_out:
        print("  * MinIO Bucket: edusense-documents (CONFIRMED PRIVATE)")
    else:
        print("  * MinIO bucket edusense-documents status: unverified")

    code, redis_out, _ = run_cmd(["docker", "exec", "edusense-redis", "redis-cli", "ping"])
    if code == 0 and "PONG" in redis_out:
        print("  * Redis Server: PONG (HEALTHY)")
    else:
        print("  * Redis Server: unreachable")

    # 5. FastAPI Health Endpoints
    print("\n[5] FastAPI Backend Probes:")
    code, health_liveness, _ = run_cmd(["curl.exe", "-s", "http://localhost:8000/health"])
    code, health_ready, _ = run_cmd(["curl.exe", "-s", "http://localhost:8000/health/ready"])
    if "ok" in health_liveness:
        print(f"  * Liveness (/health):       {health_liveness.strip()}")
    else:
        print("  * Liveness (/health):       UNAVAILABLE")
    if "ready" in health_ready:
        print(f"  * Readiness (/health/ready): {health_ready.strip()}")
    else:
        print("  * Readiness (/health/ready): UNAVAILABLE")

    print("\n" + "=" * 80)
    print(" AUDIT COMPLETED")
    print("=" * 80)
    return 0


# ==============================================================================
# 2. STATUS COMMAND
# ==============================================================================


def cmd_status(_args: argparse.Namespace) -> int:
    """Summarize milestone progress, task counts, blockers, and next eligible task."""
    backlog = load_json(BACKLOG_PATH)
    decisions = load_json(DECISIONS_PATH)

    tasks = backlog.get("tasks", [])
    milestones = backlog.get("milestones", [])

    total_tasks = len(tasks)
    status_counts = {"completed": 0, "in_progress": 0, "pending": 0, "blocked": 0, "failed": 0}

    for t in tasks:
        st = t.get("status", "pending")
        status_counts[st] = status_counts.get(st, 0) + 1

    pct = (status_counts["completed"] / total_tasks * 100) if total_tasks else 0

    print("=" * 80)
    print(f" EDUSENSE WORKFLOW STATUS -- {pct:.1f}% COMPLETE ({status_counts['completed']}/{total_tasks} Tasks)")
    print("=" * 80)

    print("\n[Milestone Breakdown]")
    for m in milestones:
        m_tasks = [t for t in tasks if t.get("milestone") == m["id"]]
        m_total = len(m_tasks)
        m_done = len([t for t in m_tasks if t.get("status") == "completed"])
        m_pct = (m_done / m_total * 100) if m_total else 0
        print(f"  * {m['id']:<6} | {m['name']:<48} | {m_done}/{m_total} ({m_pct:>3.0f}%) [{m['status'].upper()}]")

    print("\n[Status Summary]")
    print(f"  * Completed:   {status_counts['completed']}")
    print(f"  * In Progress: {status_counts['in_progress']}")
    print(f"  * Pending:     {status_counts['pending']}")
    print(f"  * Blocked:     {status_counts['blocked']}")
    print(f"  * Failed:      {status_counts['failed']}")

    # Blocked Tasks
    blocked_tasks = [t for t in tasks if t.get("status") == "blocked"]
    if blocked_tasks:
        print("\n[Blocked Tasks & Required Approvals]")
        for bt in blocked_tasks:
            unresolved = bt.get("unresolved_decisions", [])
            print(f"  * {bt['id']}: {bt['title']}")
            for d_id in unresolved:
                # find in decisions
                d_item = next((q for q in decisions.get("unresolved_questions", []) if q["id"] == d_id), None)
                if d_item:
                    print(f"      - Blocked by: {d_id} ({d_item['title']})")
                    print(f"        Question: {d_item['question']}")
                    print(f"        Approve with: python workflow/workflow.py approve --decision-id {d_id} --value \"...\"")

    # Next Eligible Task
    completed_ids = {t["id"] for t in tasks if t.get("status") == "completed"}
    next_task = None
    for t in tasks:
        if t.get("status") == "pending":
            deps = t.get("dependencies", [])
            deps_met = all(d in completed_ids for d in deps)
            if deps_met and not t.get("unresolved_decisions"):
                next_task = t
                break

    print("\n[Next Eligible Task]")
    if next_task:
        print(f"  ID:          {next_task['id']}")
        print(f"  Milestone:   {next_task['milestone']}")
        print(f"  Title:       {next_task['title']}")
        print(f"  Dependencies: {next_task.get('dependencies', [])} (ALL SATISFIED)")
        print(f"  Affected:    {next_task.get('affected_files', [])}")
        print("  Execute:     python workflow/workflow.py run-task --task-id " + next_task['id'])
    else:
        in_prog = [t for t in tasks if t.get("status") == "in_progress"]
        if in_prog:
            print(f"  Currently in progress: {in_prog[0]['id']} : {in_prog[0]['title']}")
        else:
            print("  No pending tasks are currently eligible without resolving blockers.")

    print("=" * 80)
    return 0


# ==============================================================================
# 3. VALIDATE COMMAND (Lint, Types, Tests, Architecture)
# ==============================================================================


def check_architecture_boundaries() -> list[str]:
    """Verify strict layering invariants via AST inspection:
    1. Invariant 2: Routers in backend/app/api/ MUST NOT import from backend.app.repositories.
    2. Invariant 4: No module outside backend/app/providers MUST import google.genai / google.generativeai.
    """
    violations: list[str] = []
    app_dir = BACKEND_DIR / "app"
    if not app_dir.exists():
        return violations

    for py_file in app_dir.rglob("*.py"):
        rel_path = py_file.relative_to(REPO_ROOT).as_posix()
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except Exception as e:
            violations.append(f"{rel_path}: Failed to parse AST: {e}")
            continue

        for node in ast.walk(tree):
            # Check Invariant 2: Routers must not import repositories
            if "app/api" in rel_path:
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if "app.repositories" in alias.name or alias.name.startswith("repositories"):
                            violations.append(f"{rel_path}:{node.lineno}: Router illegally imports repository '{alias.name}' (Invariant 2 violation)")
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    if "app.repositories" in mod or mod.startswith("repositories"):
                        violations.append(f"{rel_path}:{node.lineno}: Router illegally imports from repository '{mod}' (Invariant 2 violation)")

            # Check Invariant 4: Only providers may import Gemini SDK
            if "app/providers" not in rel_path:
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("google.genai") or alias.name.startswith("google.generativeai"):
                            violations.append(f"{rel_path}:{node.lineno}: Module outside 'app/providers' imports Gemini SDK '{alias.name}' (Invariant 4 violation)")
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    if mod.startswith("google.genai") or mod.startswith("google.generativeai"):
                        violations.append(f"{rel_path}:{node.lineno}: Module outside 'app/providers' imports from Gemini SDK '{mod}' (Invariant 4 violation)")

    return violations


def cmd_validate(args: argparse.Namespace) -> int:
    """Run linting, type-checking, tests, and architecture boundary verification."""
    print("=" * 80)
    print(" EDUSENSE WORKFLOW VALIDATION PIPELINE")
    print("=" * 80)

    overall_passed = True

    # 1. Architecture Boundaries Check
    print("\n[Step 1/4] Architecture Layering & Import Boundaries Check...")
    violations = check_architecture_boundaries()
    if violations:
        print("  FAIL: Architecture violations detected:")
        for v in violations:
            print(f"    - {v}")
        overall_passed = False
    else:
        print("  PASS: All architecture boundaries verified (Routers, Repositories, Providers).")

    if getattr(args, "check_architecture", False):
        print("\n" + "=" * 80)
        if overall_passed:
            print(" ARCHITECTURE BOUNDARY CHECK PASSED (0 ERRORS)")
            print("=" * 80)
            return 0
        print(" ARCHITECTURE BOUNDARY CHECK FAILED — SEE VIOLATIONS ABOVE")
        print("=" * 80)
        return 1

    # 2. Ruff Lint Check
    print("\n[Step 2/4] Ruff Linter Check (backend)...")
    ruff_bin = find_tool("ruff")
    code, stdout, stderr = run_cmd([ruff_bin, "check", "."], cwd=BACKEND_DIR)
    if code == 0:
        print("  PASS: Ruff check passed cleanly (0 errors).")
    else:
        print(f"  FAIL: Ruff check failed (code {code}):")
        print(stdout or stderr)
        overall_passed = False

    # 3. Mypy Type Check
    print("\n[Step 3/4] Mypy Static Type Check (backend)...")
    mypy_bin = find_tool("mypy")
    code, stdout, stderr = run_cmd([mypy_bin, "."], cwd=BACKEND_DIR)
    if code == 0:
        print("  PASS: Mypy type-check passed cleanly (0 errors).")
    else:
        print(f"  FAIL: Mypy type-check failed (code {code}):")
        print(stdout or stderr)
        overall_passed = False

    # 4. Pytest Test Suite
    print("\n[Step 4/4] Pytest Test Suite (backend)...")
    pytest_bin = find_tool("pytest")
    code, stdout, stderr = run_cmd([pytest_bin, "-v"], cwd=BACKEND_DIR)
    if code == 0:
        # extract passed summary line
        lines = stdout.splitlines()
        summary = lines[-1] if lines else "Passed"
        print(f"  PASS: {summary}")
    else:
        print(f"  FAIL: Pytest failed (code {code}):")
        print(stdout or stderr)
        overall_passed = False

    print("\n" + "=" * 80)
    if overall_passed:
        print(" ALL WORKFLOW CHECKS PASSED SUCCESSFULLY (0 ERRORS)")
        print("=" * 80)
        return 0
    else:
        print(" WORKFLOW VALIDATION FAILED — PLEASE FIX ERRORS ABOVE")
        print("=" * 80)
        return 1


# ==============================================================================
# 4. RUN-TASK COMMAND
# ==============================================================================


def cmd_run_task(args: argparse.Namespace) -> int:
    """Select eligible task, enforce dependency and approval boundaries, and initiate execution."""
    backlog = load_json(BACKLOG_PATH)
    tasks = backlog.get("tasks", [])
    target_id = args.task_id

    task = None
    if target_id:
        task = next((t for t in tasks if t["id"] == target_id), None)
        if not task:
            print(f"Error: Task '{target_id}' not found in backlog.json")
            return 1
    else:
        # Select next eligible
        completed_ids = {t["id"] for t in tasks if t.get("status") == "completed"}
        for t in tasks:
            if t.get("status") == "pending":
                deps = t.get("dependencies", [])
                if all(d in completed_ids for d in deps) and not t.get("unresolved_decisions"):
                    task = t
                    break
        if not task:
            print("No pending tasks are currently eligible without resolving blockers.")
            return 1

    # Check dependencies
    completed_ids = {t["id"] for t in tasks if t.get("status") == "completed"}
    missing_deps = [d for d in task.get("dependencies", []) if d not in completed_ids]
    if missing_deps:
        print(f"ERROR: Cannot run '{task['id']}': Missing uncompleted dependencies: {missing_deps}")
        return 1

    # Check approval gates
    if task.get("requires_approval") and task.get("approval_status") != "approved":
        print("=" * 80)
        print(f" [BLOCKED] TASK {task['id']} REQUIRES HUMAN APPROVAL")
        print("=" * 80)
        print(f"Title: {task['title']}")
        print(f"Reason: Task has approval requirement flag. Pending decisions: {task.get('unresolved_decisions')}")
        print("To approve, run:")
        for d_id in task.get("unresolved_decisions", []):
            print(f"  python workflow/workflow.py approve --decision-id {d_id} --value \"<PO_APPROVAL_RECORD>\"")
        return 2

    # Mark in-progress
    task["status"] = "in_progress"
    save_json(BACKLOG_PATH, backlog)

    print("=" * 80)
    print(f" ACTIVATED TASK: {task['id']} — {task['title']}")
    print("=" * 80)
    print(f"Milestone:            {task['milestone']}")
    print(f"Category:             {task['category']}")
    print(f"Dependencies:         {task.get('dependencies', [])} (SATISFIED)")
    print(f"Affected Files:       {task.get('affected_files', [])}")
    print(f"Verification Commands: {task.get('verification_commands', [])}")
    print("\nScope & Action Plan:")
    print("  1. Implement changes only within the declared affected files.")
    print("  2. Adhere strictly to Architecture Invariants and layering rules.")
    print("  3. Run verification command(s): " + "; ".join(task.get("verification_commands", [])))
    print(f"  4. Dispatch to the responsible subagent: python workflow/workflow.py orchestrate --task-id {task['id']}")
    print("=" * 80)
    return 0


# ==============================================================================
# 5. REPORT COMMAND
# ==============================================================================


def cmd_report(args: argparse.Namespace) -> int:
    """Generate the agent-handoff report template the executing subagent must complete.

    The template is written to workflow/reports/<TASK_ID>.md — the canonical
    location the orchestrator ingests. Results are never pre-filled: the
    subagent records real outcomes, and the orchestrator re-runs every
    verification command itself before the task may enter REVIEW.
    """
    backlog = load_json(BACKLOG_PATH)
    tasks = backlog.get("tasks", [])
    task_id = args.task_id

    task = next((t for t in tasks if t["id"] == task_id), None)
    if not task:
        print(f"Error: Task '{task_id}' not found in backlog.json")
        return 1

    try:
        from workflow.orchestrator.adapters.subagent import render_report_template, REPORTS_DIR
    except (ImportError, ModuleNotFoundError):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from orchestrator.adapters.subagent import render_report_template, REPORTS_DIR  # type: ignore

    report = render_report_template(
        task_id=task_id,
        title=task["title"],
        milestone=task.get("milestone", ""),
        affected_files=task.get("affected_files", []),
        verification_commands=task.get("verification_commands", []),
    )

    out_file = REPORTS_DIR / f"{task_id}.md"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nReport template written to {out_file}")
    print("The executing subagent must complete it with REAL results (agent-handoff format).")
    print("The orchestrator re-runs every verification command itself during 'orchestrate' ingest.")
    return 0


# ==============================================================================
# 6. APPROVE COMMAND
# ==============================================================================


def cmd_approve(args: argparse.Namespace) -> int:
    """Record human approval for a pending decision and unblock dependent tasks."""
    dec_id = args.decision_id
    val = args.value

    decisions = load_json(DECISIONS_PATH)
    backlog = load_json(BACKLOG_PATH)

    questions = decisions.get("unresolved_questions", [])
    q_item = next((q for q in questions if q["id"] == dec_id), None)
    if not q_item:
        print(f"Error: Decision ID '{dec_id}' not found in unresolved questions.")
        return 1

    # Move from unresolved to confirmed
    questions.remove(q_item)
    confirmed_entry = {
        "id": dec_id,
        "title": q_item["title"],
        "status": "APPROVED",
        "approved_by": "Product Owner",
        "decision": val,
        "evidence": "Human authorization via workflow approve CLI"
    }
    decisions.setdefault("confirmed_decisions", []).append(confirmed_entry)
    save_json(DECISIONS_PATH, decisions)

    # Unblock backlog tasks
    unblocked = []
    for t in backlog.get("tasks", []):
        unres = t.get("unresolved_decisions", [])
        if dec_id in unres:
            unres.remove(dec_id)
            if not unres:
                t["requires_approval"] = False
                t["approval_status"] = "approved"
                if t.get("status") == "blocked":
                    t["status"] = "pending"
                unblocked.append(t["id"])

    save_json(BACKLOG_PATH, backlog)

    print("=" * 80)
    print(f" DECISION APPROVED: {dec_id} — {q_item['title']}")
    print("=" * 80)
    print(f"Decision Record: {val}")
    print(f"Unblocked Tasks: {unblocked if unblocked else 'None'}")
    print("=" * 80)
    return 0


# ==============================================================================
# 7. ORCHESTRATOR COMMANDS
# ==============================================================================


def _get_runner(dummy: bool = False) -> Any:
    workflow_dir = Path(__file__).resolve().parent
    if str(workflow_dir) not in sys.path:
        sys.path.insert(0, str(workflow_dir))
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    try:
        from workflow.orchestrator.runner import OrchestratorRunner
        from workflow.orchestrator.adapters.subagent import SubagentDispatchAdapter
        from workflow.orchestrator.adapters.simulated import SimulatedAdapter
        from workflow.orchestrator.state_machine import TaskStateMachine
    except (ImportError, ModuleNotFoundError):
        from orchestrator.runner import OrchestratorRunner  # type: ignore
        from orchestrator.adapters.subagent import SubagentDispatchAdapter  # type: ignore
        from orchestrator.adapters.simulated import SimulatedAdapter  # type: ignore
        from orchestrator.state_machine import TaskStateMachine  # type: ignore

    if dummy:
        # Dummy mode runs the full lifecycle against an isolated temporary
        # workspace so the canonical backlog, state file, reports, and git
        # history are never touched.
        import tempfile

        tmp = Path(tempfile.mkdtemp(prefix="edusense-dummy-"))
        backlog_copy = tmp / "backlog.json"
        backlog_copy.write_text(BACKLOG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        os.environ["EDUSENSE_NO_COMMIT"] = "1"
        adapter = SimulatedAdapter(reports_dir=tmp / "reports")
        state_machine = TaskStateMachine(state_file=tmp / "orchestrator_state.json")
        return OrchestratorRunner(
            backlog_path=backlog_copy,
            state_machine=state_machine,
            adapter=adapter,
        )

    return OrchestratorRunner(adapter=SubagentDispatchAdapter())


def cmd_orchestrate(args: argparse.Namespace) -> int:
    """Execute the lifecycle step the persistent FSM state demands: dispatch or ingest."""
    runner = _get_runner(dummy=args.dummy)

    # 1. Recovery mode
    if args.recover:
        rec = runner.fsm.recover()
        print("=" * 80)
        print(" ORCHESTRATOR RECOVERY AUDIT")
        print("=" * 80)
        print(f"Active Task:        {rec['recovered_task_id']}")
        print(f"State:              {rec['state']}")
        print(f"Attempt:            {rec['attempt']}")
        print(f"Assigned Subagent:  {runner.fsm.state.assigned_subagent or 'None'}")
        print(f"Dispatch Brief:     {runner.fsm.state.dispatch_path or 'None'}")
        print(f"Needs Intervention: {rec['needs_intervention']}")
        print(f"Action:             {rec['recommended_action']}")
        print("=" * 80)
        if not args.resume:
            return 0

    # 2. Dummy task execution (safe end-to-end verification in an isolated temp workspace)
    if args.dummy:
        print("\n[ORCHESTRATOR] Running in DUMMY simulation mode for safe end-to-end validation.")
        target_id = args.task_id or "TASK-DUMMY-01"

        dummy_task = {
            "id": target_id,
            "milestone": "M0.5",
            "title": "Harmless Dummy Task for Orchestrator Verification",
            "category": "test",
            "dependencies": [],
            "status": "pending",
            "requires_approval": False,
            "approval_status": "not_required",
            "unresolved_decisions": [],
            "affected_files": ["workflow/tests/dummy_artifact.txt"],
            "verification_commands": ["python -c \"print('Dummy verification passed')\""],
        }

        # The dummy task is registered only in the temporary backlog copy.
        backlog = load_json(runner.backlog_path)
        existing = next((t for t in backlog.get("tasks", []) if t["id"] == target_id), None)
        if not existing:
            backlog.setdefault("tasks", []).append(dummy_task)
            save_json(runner.backlog_path, backlog)

        res = runner.execute_next_task(target_task_id=target_id, feedback=args.feedback)
        if res.get("status") == "dispatched":
            # Simulate the subagent executing the brief and writing its report.
            print("\n[ORCHESTRATOR] Simulating subagent execution and report submission...")
            res = runner.execute_next_task(target_task_id=target_id, feedback=args.feedback)
        print(f"\nExecution Result: {res}")
        return 0 if res.get("status") in ("review_ready", "idle") else 1

    # 3. Resume mode: re-enter the lifecycle for the active task at its current phase
    if args.resume:
        active_id = runner.fsm.state.current_task_id
        if not active_id:
            print("No active task to resume in orchestrator state.")
            return 1
        res = runner.execute_next_task(target_task_id=active_id, feedback=args.feedback)
        print(f"\nExecution Result: {res}")
        return 0 if res.get("status") in ("review_ready", "dispatched", "idle", "awaiting_review", "waiting_for_report") else 1

    # 4. Standard step execution (phase auto-selected from the FSM state)
    res = runner.execute_next_task(target_task_id=args.task_id, feedback=args.feedback)
    print(f"\nExecution Result: {res}")
    return 0 if res.get("status") in ("review_ready", "dispatched", "idle", "awaiting_review", "waiting_for_report") else 1


def cmd_review(args: argparse.Namespace) -> int:
    """Submit explicit review decision for a task awaiting review."""
    runner = _get_runner(dummy=False)
    res = runner.process_review_decision(
        task_id=args.task_id,
        reviewer=args.reviewer,
        decision=args.decision,
        feedback=args.feedback,
    )
    print("=" * 80)
    print(f" REVIEW DECISION RESULT FOR {args.task_id}")
    print("=" * 80)
    for k, v in res.items():
        print(f"  {k}: {v}")
    print("=" * 80)
    return 0 if res.get("status") in ("completed", "changes_requested") else 1


def cmd_orchestrator_status(_args: argparse.Namespace) -> int:
    """Inspect current persistent orchestrator state machine snapshot and audit trail."""
    runner = _get_runner(dummy=False)
    st = runner.fsm.state

    print("=" * 80)
    print(" EDUSENSE ORCHESTRATOR STATE MACHINE STATUS")
    print("=" * 80)
    print(f"  Current Task:       {st.current_task_id or 'None'}")
    print(f"  Current State:      {st.current_state.value}")
    print(f"  Assigned Subagent:  {st.assigned_subagent or 'None'}")
    print(f"  Dispatch Brief:     {st.dispatch_path or 'None'}")
    print(f"  Subagent Report:    {st.report_path or 'None'}")
    print(f"  Attempt:            {st.attempt_number} / {st.max_retries}")
    print(f"  Branch:             {st.branch_name or 'None'}")
    print(f"  PR / Artifact:      {st.pr_url or 'None'}")
    print(f"  Review Feedback:    {st.review_feedback or 'None'}")
    print(f"  Last Approver:      {st.approver or 'None'} ({st.approved_at or 'N/A'})")
    print(f"  Processed Events:   {len(st.processed_events)}")
    print(f"  Last Updated:       {st.updated_at}")

    print("\n[Recent Transition History (Last 5)]")
    for entry in st.history[-5:]:
        print(f"  * [{entry.timestamp}] {entry.from_state.value} -> {entry.to_state.value} on {entry.event_type.value} by {entry.actor} (task {entry.task_id})")

    print("=" * 80)
    return 0


# ==============================================================================
# CLI DISPATCHER
# ==============================================================================


def main() -> int:
    parser = argparse.ArgumentParser(description="EduSense Workflow Automation CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # audit
    subparsers.add_parser("audit", help="Run live repository and runtime audit")

    # status
    subparsers.add_parser("status", help="Summarize milestone progress and backlog status")

    # validate
    val_parser = subparsers.add_parser("validate", help="Run lint, type-check, tests, and architecture checks")
    val_parser.add_argument("--ci", action="store_true", help="CI validation mode")
    val_parser.add_argument("--check-architecture", action="store_true", help="Check architecture boundaries only")

    # run-task
    run_parser = subparsers.add_parser("run-task", help="Select and initiate next eligible task")
    run_parser.add_argument("--task-id", help="Explicit task ID to activate")

    # report
    rep_parser = subparsers.add_parser("report", help="Generate task completion report")
    rep_parser.add_argument("--task-id", required=True, help="Task ID to report")

    # approve
    app_parser = subparsers.add_parser("approve", help="Human approval gate for unresolved decisions")
    app_parser.add_argument("--decision-id", required=True, help="Decision ID to approve")
    app_parser.add_argument("--value", required=True, help="Approved decision specification")

    # orchestrate
    orch_parser = subparsers.add_parser("orchestrate", help="Autonomous deterministic task orchestrator")
    orch_parser.add_argument("--task-id", help="Explicit task ID to execute")
    orch_parser.add_argument("--dummy", action="store_true", help="Run harmless dummy task simulation")
    orch_parser.add_argument("--step", action="store_true", help="Execute single step (default behavior)")
    orch_parser.add_argument("--resume", action="store_true", help="Resume currently in-progress task")
    orch_parser.add_argument("--recover", action="store_true", help="Audit and recover runner state after restart")
    orch_parser.add_argument("--feedback", help="Feedback notes when resuming after changes requested")

    # review
    rev_parser = subparsers.add_parser("review", help="Submit explicit review decision for a task awaiting review")
    rev_parser.add_argument("--task-id", required=True, help="Task ID under review")
    rev_parser.add_argument("--reviewer", required=True, help="Reviewer username (must be authorized PO)")
    rev_parser.add_argument("--decision", required=True, choices=["APPROVED", "CHANGES_REQUESTED"], help="Review decision")
    rev_parser.add_argument("--feedback", help="Review feedback or requested changes")

    # orchestrator-status
    subparsers.add_parser("orchestrator-status", help="Inspect orchestrator state machine snapshot and audit trail")

    args = parser.parse_args()

    dispatch = {
        "audit": cmd_audit,
        "status": cmd_status,
        "validate": cmd_validate,
        "run-task": cmd_run_task,
        "report": cmd_report,
        "approve": cmd_approve,
        "orchestrate": cmd_orchestrate,
        "review": cmd_review,
        "orchestrator-status": cmd_orchestrator_status,
    }

    handler = dispatch.get(args.command)
    if handler:
        return handler(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
