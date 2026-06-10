from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class ExecutionPolicy:
    """Controls retry, timeout, concurrency, and fallback behavior for Function execution."""

    max_retries: int = 2
    retry_delay: float = 1.0
    concurrency_limit: int = 5
    timeout: float = 60.0
    fallback: Callable | None = None
