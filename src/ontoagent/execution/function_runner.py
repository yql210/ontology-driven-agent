from __future__ import annotations

import time
from typing import Any

from ontoagent.execution.action_types import ActionContext, FunctionResult
from ontoagent.execution.circuit_breaker import CircuitBreaker
from ontoagent.execution.execution_policy import ExecutionPolicy
from ontoagent.execution.functions.registry import get_function


class FunctionRunner:
    """Runs registered Functions with retry, circuit-breaker, and fallback policies."""

    def __init__(
        self,
        graph_store: Any = None,
        circuit_breaker: Any = None,
        approval_gate: Any = None,
    ) -> None:
        self._graph_store = graph_store
        self._circuit_breaker = circuit_breaker
        self._approval_gate = approval_gate
        self._policies: dict[str, ExecutionPolicy] = {}
        self._breakers: dict[str, CircuitBreaker] = {}

    def set_policy(self, func_name: str, policy: ExecutionPolicy) -> None:
        self._policies[func_name] = policy

    def run(self, func_name: str, ctx: ActionContext, bypass_approval: bool = False, **kwargs) -> FunctionResult:
        fn = get_function(func_name)
        if fn is None:
            return FunctionResult(success=False, error=f"Unknown function: {func_name}")

        # Approval check (if gate is wired)
        if self._approval_gate is not None and not bypass_approval:
            target = ctx.match_data.get("entity", {}).get("name", "")
            if not target:
                target = ctx.match_data.get("target", "")
            if target:
                from ontoagent.domain.approval import ApprovalContext, DecisionLevel
                from ontoagent.execution.constraints.policies import FunctionDangerPolicy
                from ontoagent.execution.functions.registry import get_function_meta

                approval_ctx = ApprovalContext(
                    intent_type=ctx.match_data.get("intent_type", ""),
                    target=target,
                    params=ctx.match_data,
                    entity=ctx.match_data.get("entity", {}),
                )
                meta = get_function_meta(func_name)
                danger_policy = FunctionDangerPolicy({func_name: meta})
                result = danger_policy.evaluate(approval_ctx, function_name=func_name)

                if result.level == DecisionLevel.PENDING:
                    # Need approval for this function
                    token = self._approval_gate.check(approval_ctx, function_name=func_name)
                    if token.level == DecisionLevel.PENDING:
                        return FunctionResult(
                            success=False,
                            error=f"Function '{func_name}' 需要审批 (danger_level={meta.get('danger_level', 'read')})",
                            data={
                                "approval_required": True,
                                "approval_token": token.token,
                                "function_name": func_name,
                            },
                        )

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
