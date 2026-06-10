from __future__ import annotations

import time

from layerkg.action_types import ActionContext, FunctionResult
from layerkg.circuit_breaker import CircuitBreaker
from layerkg.execution_policy import ExecutionPolicy
from layerkg.functions.registry import get_function


class FunctionRunner:
    """Runs registered Functions with retry, circuit-breaker, and fallback policies."""

    def __init__(self) -> None:
        self._policies: dict[str, ExecutionPolicy] = {}
        self._breakers: dict[str, CircuitBreaker] = {}

    def set_policy(self, func_name: str, policy: ExecutionPolicy) -> None:
        self._policies[func_name] = policy

    def run(self, func_name: str, ctx: ActionContext, **kwargs) -> FunctionResult:
        fn = get_function(func_name)
        if fn is None:
            return FunctionResult(success=False, error=f"Unknown function: {func_name}")

        breaker = self._breakers.setdefault(func_name, CircuitBreaker())

        # open → reject (try fallback). half_open → allow through (probe).
        if breaker.is_open:
            policy = self._policies.get(func_name)
            if policy and policy.fallback:
                return policy.fallback(ctx, **kwargs)
            return FunctionResult(success=False, error="Circuit breaker open")

        policy = self._policies.get(func_name, ExecutionPolicy())
        last_error: Exception | None = None

        for attempt in range(policy.max_retries + 1):
            try:
                result = fn(ctx, **kwargs)
                breaker.record_success()
                return result
            except Exception as e:
                last_error = e
                if attempt < policy.max_retries:
                    time.sleep(policy.retry_delay * (2**attempt))

        breaker.record_failure()
        return FunctionResult(success=False, error=str(last_error))

    def run_batch(self, func_names: list[str], ctx: ActionContext, **kwargs) -> list[FunctionResult]:
        results: list[FunctionResult] = []
        for name in func_names:
            results.append(self.run(name, ctx, **kwargs))
        return results
