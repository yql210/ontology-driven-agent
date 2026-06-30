"""V3.4 Action executor — replaces legacy OntologyEngine."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ontoagent.execution.action_types import ActionConfig, ActionContext, ActionResult, FunctionResult
from ontoagent.execution.constraints.guard_pipeline import ActionGuardPipeline
from ontoagent.execution.constraints.guards import EntityExistsGuard, EntityPropertyGuard
from ontoagent.execution.intent_router import build_intent_map


class ActionExecutor:
    def __init__(
        self,
        graph_store: Any,
        yaml_path: Path | None = None,
        function_runner: Any | None = None,
        guard_pipeline: ActionGuardPipeline | None = None,
        shape_registry: Any | None = None,
    ) -> None:
        self._graph_store = graph_store
        self._function_runner = function_runner
        self._guard_pipeline = guard_pipeline
        self._shape_registry = shape_registry
        if yaml_path is None:
            yaml_path = Path(__file__).parent.parent / "pipeline" / "ontology_actions.yaml"
        self._intent_map = build_intent_map(yaml_path)

    @property
    def intent_map(self) -> dict[str, ActionConfig]:
        return self._intent_map

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

        # 3. Guard pipeline check — SKIP if bypass_guard=True
        if not bypass_guard:
            pipeline = self._guard_pipeline
            if pipeline is None:
                pipeline = ActionGuardPipeline([EntityExistsGuard(), EntityPropertyGuard()])

            block_reason, warnings = pipeline.check(config, entity, self._graph_store)
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
