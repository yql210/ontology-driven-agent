from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ontoagent.domain.approval import ApprovalContext, DecisionLevel, PolicyResult


class ApprovalPolicy(ABC):
    """审批策略接口。每个策略评估审批上下文，返回一个 PolicyResult。"""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def evaluate(self, context: ApprovalContext, **kwargs: Any) -> PolicyResult: ...


class ShapeBasedGuardPolicy(ApprovalPolicy):
    """基于 Shape 约束的审批策略（Phase 5）。

    直接复用 ActionExecutor.check_with_shapes() 收集 capabilities 并评估 Shape，
    不再依赖 ActionGuardPipeline。

    配置格式:
        approval_policy:
          on_block: "require_approval" | "auto_reject"
          on_warn: "require_approval" | "auto_allow"
    """

    def __init__(
        self,
        on_block: str = "require_approval",
        on_warn: str = "require_approval",
    ) -> None:
        self._on_block = on_block
        self._on_warn = on_warn

    @property
    def name(self) -> str:
        return "ShapeBasedGuardPolicy"

    def evaluate(self, context: ApprovalContext, **kwargs: Any) -> PolicyResult:
        executor = kwargs.get("executor")
        config = kwargs.get("config")
        if executor is None or config is None:
            return PolicyResult(
                policy_name=self.name,
                level=DecisionLevel.APPROVED,
                reason="no executor or config",
            )

        block_reason, warnings = executor.check_with_shapes(context.entity, config)

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
            reason="shape check passed",
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

    auto_approve_levels / require_approval_levels 是实例属性，
    允许通过 YAML 配置覆盖默认值。
    """

    def __init__(self, function_meta: dict[str, dict[str, str]] | None = None) -> None:
        """function_meta: {function_name: {danger_level: "write", description: "..."}}"""
        self._meta = function_meta or {}
        self.auto_approve_levels: set[str] = {"read"}
        self.require_approval_levels: set[str] = {"read_sensitive", "write", "admin"}

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

        if danger_level in self.auto_approve_levels:
            return PolicyResult(
                policy_name=self.name,
                level=DecisionLevel.APPROVED,
                reason=f"function danger_level={danger_level}",
            )

        if danger_level in self.require_approval_levels:
            return PolicyResult(
                policy_name=self.name,
                level=DecisionLevel.PENDING,
                reason=f"Function '{func_name}' danger_level={danger_level}，需要审批",
                details={"function_name": func_name, "danger_level": danger_level},
            )

        # Unknown danger_level — approved by default (safe fail-open for backward compat)
        return PolicyResult(
            policy_name=self.name,
            level=DecisionLevel.APPROVED,
            reason=f"function danger_level={danger_level} (not in auto_approve or require_approval — default approved)",
        )
