"""Circuit breaker for provider calls.

States: closed (normal) -> open (failing, rejects fast) -> half_open (probing).
Threshold consecutive failures open the circuit; a cooldown elapses before a
probe is allowed; successes close it again. Pure in-process logic, no I/O —
the adapter supplies the clock for deterministic tests.
"""

import time
from enum import Enum
from typing import Any


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when a call is rejected because the circuit is open."""


class CircuitBreaker:
    """Thread-unsafe (worker-local) breaker with injectable clock and thresholds."""

    def __init__(
        self,
        failure_threshold: int = 5,
        cooldown_seconds: float = 30.0,
        clock: Any = None,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._clock = clock or (lambda: time.monotonic())
        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self.opened_at: float | None = None

    def allow_call(self) -> bool:
        """Whether a call may proceed right now (transitions open -> half_open)."""
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if self._clock() - (self.opened_at or 0.0) >= self.cooldown_seconds:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        return self.state == CircuitState.HALF_OPEN  # one probe at a time

    def record_success(self) -> None:
        """A success closes the circuit and resets the failure count."""
        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        """A failure counts; at the threshold the circuit opens."""
        self.consecutive_failures += 1
        if self.state == CircuitState.HALF_OPEN or self.consecutive_failures >= self.failure_threshold:
            self.state = CircuitState.OPEN
            self.opened_at = self._clock()

    def reset(self) -> None:
        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self.opened_at = None
