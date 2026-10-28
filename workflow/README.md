# EduSense Workflow Engine

Evidence-driven, repository-native workflow automation for the EduSense backlog
(M0–M6). The engine tracks tasks, enforces architecture invariants, manages
Product Owner approval gates, and coordinates execution through **ZCode
specialist subagents** — it never executes project code itself and never
accepts a subagent's claims as proof of success.

## Layout

```
workflow/
├── workflow.py               # CLI: audit, status, validate, run-task, report,
│                             #       approve, orchestrate, review, orchestrator-status
├── backlog.json              # Canonical machine-readable backlog (spec — do not hand-edit statuses)
├── decisions.json            # Confirmed decisions, unresolved PO questions, proposed defaults
├── orchestrator_state.json   # Persistent FSM snapshot (recovery across restarts)
├── agents/
│   └── routing.json          # Task -> subagent routing (category defaults + overrides)
├── orchestrator/
│   ├── models.py             # Task states, events, execution spec/results, persistent state
│   ├── state_machine.py      # Deterministic FSM: READY → RUNNING → REVIEW → APPROVED → DONE
│   ├── runner.py             # Two-phase runner: dispatch → ingest → review
│   └── adapters/
│       ├── base.py           # ExecutionAdapter contract (dispatch_task / ingest_report)
│       ├── subagent.py       # Routing, dispatch briefs, QA briefs, report ingestion + re-verification
│       ├── simulated.py      # In-process simulation for `orchestrate --dummy`
│       ├── shell.py          # Shared command runner (backend/.venv-aware PATH)
│       ├── github.py         # Branches, commits, PR/review artifacts, CI status, approver auth
│       └── notifier.py       # Console + webhook notifications with secret redaction
├── dispatches/               # Emitted dispatch briefs (task + QA review), per task
├── reports/                  # Subagent handoff reports ingested by the orchestrator
├── reviews/                  # PR review artifacts (local fallback when no GITHUB_TOKEN)
└── tests/                    # Dummy artifacts for lifecycle verification
```

## Subagent routing

Tasks are routed to the ZCode specialist agents by `agents/routing.json`:

| Category  | Subagent                    |
|-----------|-----------------------------|
| `backend` | `backend-engineer`          |
| `db`      | `database-engineer`         |
| `ai`      | `ai-rag-engineer`           |
| `infra`   | `worker-scheduler-engineer` |
| `workflow`| `backend-orchestrator`      |
| `test`    | `backend-qa-reviewer`       |

Per-task overrides live in `task_overrides` (e.g. `TASK-M4-02` quota manager →
`ai-rag-engineer`). The internal QA gate before Product Owner approval is
`backend-qa-reviewer`; the coordinating agent is `backend-orchestrator`.

## The task lifecycle

```
READY --orchestrate--> RUNNING --subagent implements + writes report-->
RUNNING --orchestrate (ingest + re-verify)--> REVIEW --review APPROVED--> DONE
                       ^                                        |
                       +------ review CHANGES_REQUESTED --------+
```

Calling `orchestrate` while the task is RUNNING but no report exists yet is safe
and idempotent: it returns `waiting_for_report` without consuming an attempt.

1. **Dispatch** — `python workflow/workflow.py orchestrate`
   Selects the next eligible task (dependencies satisfied, no unresolved PO
   decisions), opens the `task/<id>` branch, routes the task, and writes the
   bounded dispatch brief to `workflow/dispatches/<TASK_ID>.md`.

2. **Subagent execution** — hand the brief to the assigned subagent through
   the ZCode Agent tool (`backend-orchestrator` can do this when delegation
   tools are available). The subagent reads `AGENTS.md` + `.agents/rules/` +
   its own agent definition, implements strictly within the declared files,
   runs the declared verification commands, and writes its handoff report to
   `workflow/reports/<TASK_ID>.md` (agent-handoff format, sections 1–6).

3. **Ingest** — `python workflow/workflow.py orchestrate` again
   Reads the report, then **re-runs every declared verification command
   locally**. Only exit-code-0 evidence advances the task: commit → review
   artifact (`workflow/reviews/PR_<TASK_ID>.md` or a remote GitHub PR) → QA
   review brief for `backend-qa-reviewer` → FSM state REVIEW.

4. **Human approval** (unchanged gate) —
   `python workflow/workflow.py review --task-id <ID> --reviewer <PO_USER> --decision APPROVED`
   (requires CI checks to pass) or `--decision CHANGES_REQUESTED --feedback "..."`,
   then `orchestrate --resume` re-dispatches with the feedback attached.

Retry policy is unchanged: up to 3 attempts, after which the task lands in
FAILED and needs manual intervention.

## Fidelity rules preserved

- `backlog.json` and `decisions.json` are the only sources of truth for scope
  and gates; the engine never invents tasks or approvals.
- Every verification result is re-executed by the engine; subagent reports are
  handoffs, not evidence.
- Architecture invariants (routers never import repositories; only
  `app/providers` imports the Gemini SDK) are enforced by `validate`.
- Approval decisions are authorized against the Product Owner list
  (`decisions.json` + `AUTHORIZED_APPROVERS`).
- `orchestrate --dummy` verifies the full lifecycle in an isolated temporary
  workspace (temp backlog copy, temp state file, temp reports dir,
  `EDUSENSE_NO_COMMIT=1`), leaving canonical files untouched.

## Legacy note

Earlier iterations integrated the Google Antigravity runtime (Python SDK, `agy`
CLI, AgentAPI conversation polling). That integration has been removed; the
`conversation_id` field survives in state files only for backward
compatibility and is no longer used.
