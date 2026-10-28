"""Abstract base class for execution adapters.

EduSense task execution is delegated to specialist ZCode subagents
(backend-engineer, database-engineer, ai-rag-engineer,
worker-scheduler-engineer, backend-qa-reviewer) coordinated by
backend-orchestrator. Execution is therefore two-phase:

  1. dispatch_task: emit a bounded, ready-to-run task brief for the routed subagent.
  2. ingest_report: read the subagent's handoff report and independently re-run
     the declared verification commands as evidence.

No adapter may report task success without locally re-executed verification
evidence; a subagent's own claims are never accepted as proof.
"""

from abc import ABC, abstractmethod

from ..models import TaskExecutionSpec, TaskExecutionResult


class ExecutionAdapter(ABC):
    """Abstract interface for subagent-oriented task execution."""

    @abstractmethod
    def is_available(self) -> tuple[bool, str]:
        """Return (is_ready, diagnostic_details) for this adapter's runtime requirements."""
        ...

    @abstractmethod
    def dispatch_task(self, spec: TaskExecutionSpec) -> TaskExecutionResult:
        """Phase 1: route the task to its subagent and emit the dispatch brief."""
        ...

    @abstractmethod
    def ingest_report(self, spec: TaskExecutionSpec) -> TaskExecutionResult:
        """Phase 2: ingest the subagent handoff report and re-verify declared commands locally."""
        ...
