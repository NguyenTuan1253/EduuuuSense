"""Execution and integration adapters for EduSense Orchestrator."""

from .base import ExecutionAdapter
from .subagent import SubagentDispatchAdapter
from .simulated import SimulatedAdapter
from .github import GitHubAdapter
from .notifier import Notifier

__all__ = [
    "ExecutionAdapter",
    "SubagentDispatchAdapter",
    "SimulatedAdapter",
    "GitHubAdapter",
    "Notifier",
]
