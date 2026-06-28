from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from ontoagent.domain.approval import ApprovalContext, DecisionLevel, PolicyResult

if TYPE_CHECKING:
    from ontoagent.execution.constraints.guard_pipeline import ActionGuardPipeline


class ApprovalPolicy(ABC):
    """审批策略接口。每个策略评估审批上下文，返回一个 PolicyResult。"""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def evaluate(self, context: ApprovalContext, **kwargs: Any) -> PolicyResult: ...


class GuardResultPolicy(ApprovalPolicy):
    """根据 Guard Pipeline 结果 + 配置决定是否触发审批。

    配置格式:
        approval_policy:
          on_block: "require_approval" | "auto_reject"
          on_warn: "require_approval" | "auto_allow"

    Pipeline 可以通过 set_pipeline() 延迟注入，解决 _get_action_executor 的时序问题。
    """

    def __init__(
        self,
        pipeline: ActionGuardPipeline | None = None,
        on_block: str = "require_approval",
        on_warn: str = "require_approval",
    ) -> None:
        self._pipeline = pipeline
        self._on_block = on_block
        self._on_warn = on_warn

    def set_pipeline(self, pipeline: ActionGuardPipeline) -> None:
        """延迟注入 GuardPipeline（解决 ApprovalGate 与 ActionExecutor 的初始化时序）。"""
        self._pipeline = pipeline

    @property
    def name(self) -> str:
        return "GuardResultPolicy"

    def evaluate(self, context: ApprovalContext, **kwargs: Any) -> PolicyResult:
        config = kwargs.get("config")
        graph_store = kwargs.get("graph_store")
        if config is None or graph_store is None:
            return PolicyResult(
                policy_name=self.name,
                level=DecisionLevel.APPROVED,
                reason="no config or graph_store",
            )

        # Guard against missing pipeline (not yet wired)
        if self._pipeline is None:
            return PolicyResult(
                policy_name=self.name,
                level=DecisionLevel.APPROVED,
                reason="pipeline not yet wired",
            )

        block_reason, warnings = self._pipeline.check(config, context.entity, graph_store)

        # Check guard results
        if block_reason:
            if self._on_block == "auto_reject":
                return PolicyResult(
                    policy_name=self.name,
                    level=DecisionLevel.DENIED,
                    reason=block_reason,
                )
            else:  # require_approval
                return PolicyResult(
                    policy_name=self.name,
                    level=DecisionLevel.PENDING,
                    reason=block_reason,
                    details={"guard_block": block_reason, "warnings": warnings},
                )

        if warnings and self._on_warn == "require_approval":
            return PolicyResult(
                policy_name=self.name,
                level=DecisionLevel.PENDING,
                reason="WARN 级别约束需要确认",
                details={"warnings": warnings},
            )

        return PolicyResult(
            policy_name=self.name,
            level=DecisionLevel.APPROVED,
            reason="guard check passed",
        )


class ActionApprovalPolicy(ApprovalPolicy):
    """检查 ActionConfig.requires_approval 字段"""

    @property
    def name(self) -> str:
        return "ActionApprovalPolicy"

    def evaluate(self, context: ApprovalContext, **kwargs: Any) -> PolicyResult:
        config = kwargs.get("config")
        if config is None:
            return PolicyResult(
                policy_name=self.name,
                level=DecisionLevel.APPROVED,
                reason="no config",
            )

        if getattr(config, "requires_approval", False):
            return PolicyResult(
                policy_name=self.name,
                level=DecisionLevel.PENDING,
                reason=f"Action '{context.intent_type}' 需要审批",
            )
        return PolicyResult(
            policy_name=self.name,
            level=DecisionLevel.APPROVED,
            reason="action does not require approval",
        )


class FunctionDangerPolicy(ApprovalPolicy):
    """根据 function 的 danger_level 决定是否触发审批。

    danger_level:
      - "read": 纯查询，自动放行
      - "read_sensitive": 查询敏感数据，需要审批
      - "write": 修改数据，需要审批
      - "admin": 系统级操作，需要审批
    """

    # 不需要审批的危险级别
    AUTO_APPROVE = {"read"}

    def __init__(self, function_meta: dict[str, dict[str, str]] | None = None) -> None:
        """function_meta: {function_name: {danger_level: "write", description: "..."}}"""
        self._meta = function_meta or {}

    @property
    def name(self) -> str:
        return "FunctionDangerPolicy"

    def evaluate(self, context: ApprovalContext, **kwargs: Any) -> PolicyResult:
        func_name = kwargs.get("function_name", "")
        if not func_name:
            return PolicyResult(
                policy_name=self.name,
                level=DecisionLevel.APPROVED,
                reason="no function name",
            )

        meta = self._meta.get(func_name, {})
        danger_level = meta.get("danger_level", "read")

        if danger_level in self.AUTO_APPROVE:
            return PolicyResult(
                policy_name=self.name,
                level=DecisionLevel.APPROVED,
                reason=f"function danger_level={danger_level}",
            )

        return PolicyResult(
            policy_name=self.name,
            level=DecisionLevel.PENDING,
            reason=f"Function '{func_name}' danger_level={danger_level}，需要审批",
            details={"function_name": func_name, "danger_level": danger_level},
        )
