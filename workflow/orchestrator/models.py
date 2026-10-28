"""Domain models and data schemas for EduSense Autonomous Task Orchestrator.

Implemented with stdlib dataclasses only: the workflow engine is a
zero-extra-dependency CLI and must run on any Python 3.10+ interpreter
without third-party packages.
"""

from dataclasses import dataclass, field, fields as dataclass_fields
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class TaskState(str, Enum):
    """Explicit state of a task in the Orchestrator finite state machine."""
    READY = "READY"
    RUNNING = "RUNNING"
    REVIEW = "REVIEW"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    BLOCKED = "BLOCKED"
    APPROVED = "APPROVED"
    DONE = "DONE"
    FAILED = "FAILED"


class EventType(str, Enum):
    """Explicit event triggers that transition the FSM between states."""
    START_TASK = "START_TASK"
    TASK_COMPLETED_SUCCESS = "TASK_COMPLETED_SUCCESS"
    TASK_FAILED = "TASK_FAILED"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    APPROVE_TASK = "APPROVE_TASK"
    FINALIZE_TASK = "FINALIZE_TASK"
    ENCOUNTERED_BLOCKER = "ENCOUNTERED_BLOCKER"
    UNBLOCK_TASK = "UNBLOCK_TASK"


@dataclass
class AuditLogEntry:
    """Immutable audit record for every state transition in the orchestrator."""
    event_id: str
    event_type: EventType
    task_id: str
    from_state: TaskState
    to_state: TaskState
    actor: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "task_id": self.task_id,
            "from_state": self.from_state.value,
            "to_state": self.to_state.value,
            "actor": self.actor,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuditLogEntry":
        return cls(
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
            event_id=data.get("event_id", ""),
            event_type=EventType(data.get("event_type", "START_TASK")),
            task_id=data.get("task_id", ""),
            from_state=TaskState(data.get("from_state", "READY")),
            to_state=TaskState(data.get("to_state", "READY")),
            actor=data.get("actor", "orchestrator"),
            details=data.get("details", {}) or {},
        )


@dataclass
class TaskExecutionSpec:
    """Complete, self-contained specification passed to the execution adapter.

    `subagent` names the ZCode specialist agent responsible for the task
    (e.g. backend-engineer, database-engineer, ai-rag-engineer,
    worker-scheduler-engineer, backend-qa-reviewer).
    """
    task_id: str
    milestone: str
    title: str
    category: str
    dependencies: list[str] = field(default_factory=list)
    affected_files: list[str] = field(default_factory=list)
    verification_commands: list[str] = field(default_factory=list)
    review_feedback: Optional[str] = None
    attempt_number: int = 1
    subagent: Optional[str] = None
    branch_name: Optional[str] = None
    conversation_id: Optional[str] = None  # legacy field retained for old state-file compatibility


@dataclass
class VerificationResult:
    """Individual verification command execution record."""
    command: str
    exit_code: int
    passed: bool
    stdout: str = ""
    stderr: str = ""


@dataclass
class TaskExecutionResult:
    """Result returned by an execution adapter for one lifecycle phase.

    - dispatch phase: success means a dispatch brief was emitted for the routed subagent.
    - ingest phase: success means the subagent report was ingested AND every declared
      verification command was re-executed locally with exit code 0.
    """
    success: bool
    task_id: str
    attempt_number: int
    subagent: Optional[str] = None
    brief_path: Optional[str] = None
    report_path: Optional[str] = None
    report_submitted: bool = False  # True once a subagent handoff report exists on disk
    modified_files: list[str] = field(default_factory=list)
    verification_results: list[VerificationResult] = field(default_factory=list)
    error_message: Optional[str] = None
    agent_output: str = ""
    conversation_id: Optional[str] = None  # legacy field retained for old state-file compatibility


@dataclass
class OrchestratorState:
    """Persistent orchestrator state machine snapshot for recovery after runner restart."""
    current_task_id: Optional[str] = None
    assigned_subagent: Optional[str] = None
    dispatch_path: Optional[str] = None
    report_path: Optional[str] = None
    current_state: TaskState = TaskState.READY
    attempt_number: int = 0
    max_retries: int = 3
    branch_name: Optional[str] = None
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None
    review_feedback: Optional[str] = None
    approver: Optional[str] = None
    approved_at: Optional[str] = None
    conversation_id: Optional[str] = None  # legacy field retained for old state-file compatibility
    processed_events: list[str] = field(default_factory=list)
    history: list[AuditLogEntry] = field(default_factory=list)
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_task_id": self.current_task_id,
            "assigned_subagent": self.assigned_subagent,
            "dispatch_path": self.dispatch_path,
            "report_path": self.report_path,
            "current_state": self.current_state.value,
            "attempt_number": self.attempt_number,
            "max_retries": self.max_retries,
            "branch_name": self.branch_name,
            "pr_number": self.pr_number,
            "pr_url": self.pr_url,
            "review_feedback": self.review_feedback,
            "approver": self.approver,
            "approved_at": self.approved_at,
            "conversation_id": self.conversation_id,
            "processed_events": list(self.processed_events),
            "history": [h.to_dict() for h in self.history],
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OrchestratorState":
        """Tolerantly load a persisted snapshot, ignoring unknown keys."""
        known = {f.name for f in dataclass_fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in known}
        if "current_state" in kwargs and not isinstance(kwargs["current_state"], TaskState):
            kwargs["current_state"] = TaskState(kwargs["current_state"])
        kwargs["history"] = [AuditLogEntry.from_dict(h) for h in (data.get("history") or [])]
        kwargs["processed_events"] = list(data.get("processed_events") or [])
        return cls(**kwargs)
