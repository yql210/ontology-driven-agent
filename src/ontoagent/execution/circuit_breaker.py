from __future__ import annotations

import time


class CircuitBreaker:
    """Three-state circuit breaker: closed → open → half_open."""

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 30.0) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._failure_count: int = 0
        self._state: str = "closed"  # closed | open | half_open
        self._last_failure_time: float = 0.0

    @property
    def state(self) -> str:
        if self._state == "open" and time.monotonic() - self._last_failure_time >= self._recovery_timeout:
            self._state = "half_open"
        return self._state

    @property
    def is_open(self) -> bool:
        return self.state == "open"

    @property
    def failure_count(self) -> int:
        return self._failure_count

    def record_success(self) -> None:
        self._failure_count = 0
        self._state = "closed"

    def record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._failure_count >= self._failure_threshold:
            self._state = "open"
