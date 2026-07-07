from __future__ import annotations

import logging
from typing import Any

from ontoagent.domain.approval import (
    ApprovalContext,
    ApprovalDecision,
    DecisionLevel,
    PendingApproval,
    PolicyResult,
    generate_token,
)
from ontoagent.execution.constraints.policies import ApprovalPolicy

logger = logging.getLogger(__name__)


class ApprovalGate:
    """集中审批引擎 — 策略链 → 决策 → 令牌管理 → 审批解析。"""

    def __init__(self, policies: list[ApprovalPolicy] | None = None, ttl: int = 600, max_pending: int = 10) -> None:
        self._policies: list[ApprovalPolicy] = policies or []
        self._pending: dict[str, PendingApproval] = {}
        self._audit_log: list[dict[str, Any]] = []
        self._ttl = ttl
        self._max_pending = max_pending

    @property
    def policies(self) -> list[ApprovalPolicy]:
        return self._policies

    def add_policy(self, policy: ApprovalPolicy) -> None:
        self._policies.append(policy)

    def check(
        self,
        context: ApprovalContext,
        **policy_kwargs: Any,
    ) -> ApprovalDecision:
        """运行策略链，返回审批决策。

        APPROVED → 放行
        PENDING → 生成令牌，等待人工
        DENIED → 拒绝
        """
        results: list[PolicyResult] = []

        for policy in self._policies:
            result = policy.evaluate(context, **policy_kwargs)
            results.append(result)

            if result.level == DecisionLevel.DENIED:
                self._audit("denied", context, results)
                return ApprovalDecision(level=DecisionLevel.DENIED, results=results)

        # Check for PENDING
        pending = [r for r in results if r.level == DecisionLevel.PENDING]
        if pending:
            # Check max_pending limit
            if len(self._pending) >= self._max_pending:
                self.cleanup_expired()
                if len(self._pending) >= self._max_pending:
                    return ApprovalDecision(
                        level=DecisionLevel.DENIED,
                        results=results,
                    )
            token = generate_token(context.intent_type, context.target, context.session_id)
            self._pending[token] = PendingApproval(token=token, context=context, ttl=self._ttl)
            self._audit("pending", context, results, token=token)
            return ApprovalDecision(level=DecisionLevel.PENDING, token=token, results=results)

        self._audit("approved", context, results)
        return ApprovalDecision(level=DecisionLevel.APPROVED, results=results)

    def resolve(self, token: str, approved: bool) -> ApprovalContext | None:
        """解析审批令牌。

        Args:
            token: 审批令牌
            approved: True=批准, False=拒绝

        Returns:
            批准或拒绝时返回 ApprovalContext（含原始操作上下文）；
            过期/无效令牌返回 None。

        注意：拒绝（approved=False）时也返回 context，以便调用方区分
        "令牌无效"与"令牌有效但用户拒绝"。调用方应先检查 approved 参数
        再判断返回值是否为 None。
        """
        pending = self._pending.get(token)
        if pending is None:
            logger.warning("Approval token not found or expired: %s", token)
            return None

        if pending.is_expired:
            del self._pending[token]
            logger.info("Approval token expired: %s", token)
            return None

        # Consume token (one-time use)
        del self._pending[token]
        pending.resolved = True

        if not approved:
            self._audit("rejected", pending.context, [], token=token)
            return pending.context

        self._audit("resolved", pending.context, [], token=token, approved=True)
        return pending.context

    def _audit(
        self,
        action: str,
        context: ApprovalContext,
        results: list[PolicyResult],
        token: str = "",
        approved: bool = False,
    ) -> None:
        import time

        self._audit_log.append(
            {
                "timestamp": time.time(),
                "action": action,
                "intent_type": context.intent_type,
                "target": context.target,
                "token": token,
                "approved": approved,
                "results": [{"policy": r.policy_name, "level": r.level.value, "reason": r.reason} for r in results],
            }
        )

    @property
    def audit_log(self) -> list[dict[str, Any]]:
        return self._audit_log

    def pending_count(self) -> int:
        return len(self._pending)

    def cleanup_expired(self) -> int:
        """清理过期令牌，返回清理数量"""
        expired = [t for t, p in self._pending.items() if p.is_expired]
        for t in expired:
            del self._pending[t]
        return len(expired)

    # ---- DAG-level approval (V5 Phase 5) ----

    def check_dag(
        self,
        preflight: Any,  # PreflightResult
        context: ApprovalContext,
    ) -> dict[str, str | None]:
        """Generate per-node approval tokens from a PreflightResult.

        Each node that triggered ESCALATE gets a one-time approval token.
        Blocked nodes get None (cannot proceed).
        Other nodes get None (auto-approved).

        Args:
            preflight: PreflightResult from DAGOrchestrator.preflight().
            context: Base ApprovalContext for session scoping.

        Returns:
            Dict mapping node_id → token (str for approval, None for auto/blocked).
        """
        tokens: dict[str, str | None] = {}

        for node_id in preflight.escalate_nodes:
            token = generate_token(f"dag:{node_id}", context.target, context.session_id)
            self._pending[token] = PendingApproval(
                token=token,
                context=ApprovalContext(
                    intent_type=f"dag_node:{node_id}",
                    target=context.target,
                    params=context.params,
                    entity=context.entity,
                    guard_checks=[],
                    session_id=context.session_id,
                ),
                ttl=self._ttl,
            )
            tokens[node_id] = token

        for node_id in preflight.blocked_nodes:
            tokens[node_id] = None

        return tokens

    def resolve_node(self, token: str, approved: bool) -> bool:
        """Resolve a single DAG node approval token.

        Args:
            token: Approval token from check_dag().
            approved: True to approve, False to reject.

        Returns:
            True if the node can now proceed.
        """
        ctx = self.resolve(token, approved)
        return ctx is not None
