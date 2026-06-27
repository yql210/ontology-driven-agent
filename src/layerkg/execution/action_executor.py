"""V3.4 Action executor — replaces legacy OntologyEngine."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from layerkg.execution.action_types import ActionConfig, ActionContext, ActionResult, FunctionResult
from layerkg.execution.intent_router import build_intent_map


class ActionExecutor:
    def __init__(
        self,
        graph_store: Any,
        yaml_path: Path | None = None,
        function_runner: Any | None = None,
    ) -> None:
        self._graph_store = graph_store
        self._function_runner = function_runner
        if yaml_path is None:
            yaml_path = Path(__file__).parent.parent / "pipeline" / "ontology_actions.yaml"
        self._intent_map = build_intent_map(yaml_path)

    @property
    def intent_map(self) -> dict[str, ActionConfig]:
        return self._intent_map

    def execute(self, intent_type: str, params: dict[str, Any]) -> ActionResult:
        """Execute an action: intent routing → criteria check → function chain."""
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

        # 3. Submission criteria check
        criteria_error = self._check_criteria(config, entity)
        if criteria_error:
            return ActionResult(
                success=False,
                action_name=config.name,
                error=criteria_error,
            )

        # 4. Build ActionContext
        ctx = ActionContext(
            graph_store=self._graph_store,
            match_data={**params, "entity": entity, "entity_id": entity.get("id", "")},
        )

        # 5. Execute function chain sequentially
        results: list[FunctionResult] = []
        for func_name in config.functions:
            if self._function_runner is not None:
                result = self._function_runner.run(func_name, ctx)
            else:
                result = ctx.call_function(func_name)
            results.append(result)
            if not result.success:
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

    def _resolve_entity(self, target: str) -> dict | None:
        if not target:
            return None
        cypher = (
            "MATCH (n {name: $name}) RETURN n.id AS id, n.name AS name, "
            "n.lines AS lines, n.branches AS branches, n.entityType AS entityType, "
            "labels(n) AS labels LIMIT 1"
        )
        records = self._graph_store.query(cypher, {"name": target})
        if not records:
            cypher = (
                "MATCH (n) WHERE n.name CONTAINS $name RETURN n.id AS id, n.name AS name, "
                "n.lines AS lines, n.branches AS branches, n.entityType AS entityType, "
                "labels(n) AS labels LIMIT 1"
            )
            records = self._graph_store.query(cypher, {"name": target})
        return records[0] if records else None

    def _check_criteria(self, config: ActionConfig, entity: dict) -> str | None:
        """Check submission criteria. Returns None if passed, else error message."""
        for criterion in config.submission_criteria:
            if criterion == "entity exists":
                if not entity:
                    return "目标实体不存在"
            elif criterion.startswith("entity."):
                field_expr = criterion[7:]  # strip "entity."
                try:
                    field, op, value = field_expr.split()
                    actual = entity.get(field)
                    if actual is not None:
                        if op == ">" and not (actual > int(value)):
                            return f"不满足条件: {field}({actual}) <= {value}"
                        if op == ">=" and not (actual >= int(value)):
                            return f"不满足条件: {field}({actual}) < {value}"
                except (ValueError, TypeError):
                    pass
        return None
