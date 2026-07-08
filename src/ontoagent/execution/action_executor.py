"""V3.4 Action executor — replaces legacy OntologyEngine."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ontoagent.domain.shapes import Operation, Severity
from ontoagent.execution.action_types import ActionConfig, ActionContext, ActionResult, FunctionResult
from ontoagent.execution.decision_fuser import DecisionFuser
from ontoagent.execution.functions.registry import get_capabilities
from ontoagent.execution.intent_router import build_intent_map
from ontoagent.execution.shape_evaluator import ShapeEvaluator

logger = logging.getLogger(__name__)


class ActionExecutor:
    def __init__(
        self,
        graph_store: Any,
        yaml_path: Path | None = None,
        function_runner: Any | None = None,
        shape_registry: Any | None = None,
    ) -> None:
        self._graph_store = graph_store
        self._function_runner = function_runner
        self._shape_registry = shape_registry
        if yaml_path is None:
            yaml_path = Path(__file__).parent.parent / "pipeline" / "ontology_actions.yaml"
        self._intent_map = build_intent_map(yaml_path)

    @property
    def intent_map(self) -> dict[str, ActionConfig]:
        return self._intent_map

    # ------------------------------------------------------------------
    # Public API — used by agent/tools.py and API layer
    # ------------------------------------------------------------------

    @property
    def shape_registry(self) -> Any:
        """Return the ShapeRegistry (or None if not configured)."""
        return self._shape_registry

    def resolve_entity(self, target: str) -> dict | None:
        """Resolve an entity name to its graph record (public API).

        Tries exact match first, then falls back to CONTAINS lookup.
        Returns None if not found.
        """
        return self._resolve_entity(target)

    def check_with_shapes(
        self,
        entity: dict,
        config: ActionConfig,
    ) -> tuple[str | None, list[str]]:
        """Run Shape-based constraint evaluation (public API).

        Returns (block_reason | None, warnings).
        """
        return self._check_with_shapes(entity, config)

    def execute(
        self,
        intent_type: str,
        params: dict[str, Any],
        bypass_guard: bool = False,
        bypass_function_approval: bool = False,
    ) -> ActionResult:
        """Execute an action: intent routing → criteria check → function chain."""
        import json

        # 1. Intent routing
        config = self._intent_map.get(intent_type)
        if config is None:
            return ActionResult(
                success=False,
                action_name=intent_type,
                error=f"未知操作类型: {intent_type}",
            )

        # 2. Resolve target entity
        target = params.get("target", "")
        entity = self._resolve_entity(target)
        if entity is None:
            return ActionResult(
                success=False,
                action_name=config.name,
                error=f"未找到实体 '{target}'",
            )

        # 3. Constraint check — SKIP if bypass_guard=True
        if not bypass_guard:
            if self._shape_registry is not None:
                # Phase 3c: shape-based path (ShapeEvaluator + DecisionFuser)
                block_reason, warnings = self._check_with_shapes(entity, config)
            else:
                block_reason, warnings = None, []
            if block_reason:
                return ActionResult(
                    success=False,
                    action_name=config.name,
                    error=block_reason,
                    warnings=warnings,
                )

        # 4. Build ActionContext
        ctx = ActionContext(
            graph_store=self._graph_store,
            match_data={**params, "entity": entity, "entity_id": entity.get("id", ""), "intent_type": intent_type},
        )

        # 5. Execute function chain sequentially
        results: list[FunctionResult] = []
        for func_name in config.functions:
            if self._function_runner is not None:
                result = self._function_runner.run(func_name, ctx, bypass_approval=bypass_function_approval)
            else:
                result = ctx.call_function(func_name)
            results.append(result)
            if not result.success:
                # 检测是否为 function 级审批请求（不当作普通错误）
                if result.data.get("approval_required"):
                    return ActionResult(
                        success=False,
                        action_name=config.name,
                        results=results,
                        error=f"Function '{func_name}' 需要审批",
                        warnings=[json.dumps(result.data, ensure_ascii=False)],
                    )
                return ActionResult(
                    success=False,
                    action_name=config.name,
                    results=results,
                    error=f"Function '{func_name}' failed: {result.error}",
                )

        # 6. Success
        return ActionResult(
            success=True,
            action_name=config.name,
            results=results,
            summary=f"操作 '{config.name}' 执行成功",
        )

    def _check_with_shapes(
        self,
        entity: dict,
        config: ActionConfig,
    ) -> tuple[str | None, list[str]]:
        """Phase 3c: shape-based constraint check.

        收集 ``config.functions`` 中每个 Function 声明的 capabilities
        (``"Resource:OP"`` 字符串列表) → 去重得到 Operation 集合 → 调用
        ShapeEvaluator 评估 → DecisionFuser 融合。

        Args:
            entity: 已 resolve 的目标实体字典（含 ``id`` / ``labels``）。
            config: 当前 Action 的配置，functions 字段决定能力集合。

        Returns:
            ``(block_reason | None, warnings)``，与 ``ActionGuardPipeline.check()``
            返回值语义一致：BLOCK/ESCALATE → block_reason 非 None；WARN → 警告入列表。
        """
        operations = self._collect_operations(config.functions)
        if not operations:
            return None, []

        evaluator = ShapeEvaluator(self._shape_registry, self._graph_store)
        results = evaluator.evaluate(entity, operations)
        report = DecisionFuser.fuse(results)

        if report.severity is Severity.BLOCK:
            return report.suggestion or "操作被约束 Shape 阻断", []
        if report.severity is Severity.ESCALATE:
            # 不阻断：把 triggered shapes 放入 warnings，标记 approval_required
            # 供调用方（ApprovalGate）后续路由人工审批
            escalated = report.suggestion or "操作需要人工审批"
            triggered_info = [
                f"shape:{t['shape_id']}[{t['severity']}] {t.get('evidence', {}).get('field', '')}"
                for t in report.triggered
            ]
            return None, [escalated, f"approval_required: {', '.join(triggered_info)}"]
        if report.severity is Severity.WARN:
            return None, [report.suggestion] if report.suggestion else []
        return None, []

    @staticmethod
    def _collect_operations(function_names: list[str]) -> list[Operation]:
        """从 Function 列表汇总 Operation 集合（去重、保持插入顺序）。

        capabilities 字符串格式为 ``"Resource:OP"``，此处只关心 OP 部分
        （resource 匹配由 ShapeEvaluator 通过 entity.labels 完成）。
        无法解析的条目记 warning 后跳过，不抛异常。
        """
        operations: list[Operation] = []
        seen: set[str] = set()
        for func_name in function_names:
            for cap in get_capabilities(func_name):
                if ":" not in cap:
                    continue
                op_str = cap.split(":", 1)[1]
                if op_str in seen:
                    continue
                try:
                    operations.append(Operation(op_str))
                    seen.add(op_str)
                except ValueError:
                    logger.warning("未知 capability operation %r (function=%s)", op_str, func_name)
        return operations

    def _resolve_entity(self, target: str) -> dict | None:
        if not target:
            return None
        cypher = (
            "MATCH (n {name: $name}) RETURN n.id AS id, n.name AS name, "
            "n.lines AS lines, n.branches AS branches, n.entityType AS entityType, "
            "labels(n) AS labels ORDER BY n.filePath, n.id LIMIT 1"
        )
        records = self._graph_store.query(cypher, {"name": target})
        if not records:
            cypher = (
                "MATCH (n) WHERE n.name CONTAINS $name RETURN n.id AS id, n.name AS name, "
                "n.lines AS lines, n.branches AS branches, n.entityType AS entityType, "
                "labels(n) AS labels ORDER BY n.filePath, n.id LIMIT 1"
            )
            records = self._graph_store.query(cypher, {"name": target})
        return records[0] if records else None
