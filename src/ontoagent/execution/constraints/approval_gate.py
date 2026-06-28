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

    def __init__(self, policies: list[ApprovalPolicy] | None = None) -> None:
        self._policies: list[ApprovalPolicy] = policies or []
        self._pending: dict[str, PendingApproval] = {}
        self._audit_log: list[dict[str, Any]] = []

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
            token = generate_token(context.intent_type, context.target, context.session_id)
            self._pending[token] = PendingApproval(token=token, context=context)
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
            批准的 ApprovalContext（成功时）或 None（拒绝/过期/无效令牌）
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
            return None

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
