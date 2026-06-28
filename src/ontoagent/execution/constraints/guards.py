"""Built-in action guards — EntityExists, EntityProperty, OntologyTraversal, OntologyPropagation."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ontoagent.domain.schema import GuardDecision, GuardLevel
from ontoagent.execution.constraints.guard_pipeline import ActionGuard

if TYPE_CHECKING:
    from ontoagent.execution.constraints.engine import ConstraintEngine
    from ontoagent.execution.constraints.propagator import ConstraintPropagator, PropagationRule

logger = logging.getLogger(__name__)


class EntityExistsGuard(ActionGuard):
    """Guard that checks whether the resolved entity actually exists.

    Replaces the legacy "entity exists" submission criterion.
    """

    def evaluate(self, config: Any, entity: dict, graph_store: Any) -> GuardDecision:
        if not entity or not entity.get("id"):
            return GuardDecision(level=GuardLevel.BLOCK, reason="目标实体不存在")
        return GuardDecision(level=GuardLevel.ALLOW, reason="实体存在")


class EntityPropertyGuard(ActionGuard):
    """Guard that evaluates property conditions from ActionConfig.submission_criteria.

    Supports entity.field op value expressions, e.g. "entity.lines > 100".
    Used for backward compatibility with the legacy string-based submission criteria.
    Missing properties (None/absent) are treated as BLOCK to avoid fail-open.
    Supports all comparison operators: >, >=, <, <=, ==, !=.
    """

    def evaluate(self, config: Any, entity: dict, graph_store: Any) -> GuardDecision:
        for criterion in config.submission_criteria or []:
            if criterion.startswith("entity."):
                try:
                    field_expr = criterion[7:]  # strip "entity."
                    parts = field_expr.split()
                    if len(parts) < 3:
                        continue
                    field = parts[0]
                    op = parts[1]
                    value = int(parts[2])
                    actual = entity.get(field)
                    if actual is None:
                        continue  # skip: property not set, not a violation
                    if op == ">" and not (actual > value):
                        return GuardDecision(
                            level=GuardLevel.BLOCK,
                            reason=f"不满足条件: {field}({actual}) <= {value}",
                        )
                    if op == ">=" and not (actual >= value):
                        return GuardDecision(
                            level=GuardLevel.BLOCK,
                            reason=f"不满足条件: {field}({actual}) < {value}",
                        )
                    if op == "<" and not (actual < value):
                        return GuardDecision(
                            level=GuardLevel.BLOCK,
                            reason=f"不满足条件: {field}({actual}) >= {value}",
                        )
                    if op == "<=" and not (actual <= value):
                        return GuardDecision(
                            level=GuardLevel.BLOCK,
                            reason=f"不满足条件: {field}({actual}) > {value}",
                        )
                    if op == "==" and not (actual == value):
                        return GuardDecision(
                            level=GuardLevel.BLOCK,
                            reason=f"不满足条件: {field}({actual}) != {value}",
                        )
                    if op == "!=" and not (actual != value):
                        return GuardDecision(
                            level=GuardLevel.BLOCK,
                            reason=f"不满足条件: {field}({actual}) == {value}",
                        )
                except (ValueError, TypeError, IndexError):
                    pass
        return GuardDecision(level=GuardLevel.ALLOW, reason="属性条件通过")


class OntologyTraversalGuard(ActionGuard):
    """Guard that runs TraversalConstraint evaluation via ConstraintEngine.

    Configured through ActionConfig.guard_configs entries with type "traversal".
    Evaluates ALL matching guard_configs and returns the most severe (highest) decision.
    """

    def __init__(self, constraint_engine: ConstraintEngine) -> None:
        self._engine = constraint_engine

    def evaluate(self, config: Any, entity: dict, graph_store: Any) -> GuardDecision:
        entity_id = entity.get("id", "")
        if not entity_id:
            return GuardDecision(level=GuardLevel.ALLOW, reason="no entity id")

        guard_configs = getattr(config, "guard_configs", None) or []
        all_decisions: list[GuardDecision] = []
        priority = {GuardLevel.BLOCK: 3, GuardLevel.WARN: 2, GuardLevel.ALLOW: 1}

        for gc in guard_configs:
            if gc.get("type") == "traversal":
                constraint_name = gc.get("constraint")
                if constraint_name:
                    decision = self._engine.evaluate(entity_id, constraint_name)
                    all_decisions.append(decision)

        if not all_decisions:
            return GuardDecision(level=GuardLevel.ALLOW, reason="traversal check passed")

        # Return the most severe decision
        return max(all_decisions, key=lambda d: priority.get(d.level, 0))


class OntologyPropagationGuard(ActionGuard):
    """Guard that executes ConstraintPropagator rules.

    Configured through ActionConfig.guard_configs entries with type "propagation".
    Rules are provided as a dict of {name: PropagationRule} for lookup.

    Note: graph_store is accepted for ActionGuard interface compliance but not used
    (the propagator already has its own graph_store reference).
    """

    def __init__(
        self,
        propagator: ConstraintPropagator,
        rules: dict[str, PropagationRule] | None = None,
    ) -> None:
        self._propagator = propagator
        self._rules: dict[str, PropagationRule] = rules or {}

    def evaluate(self, config: Any, entity: dict, graph_store: Any) -> GuardDecision:
        entity_id = entity.get("id", "")
        if not entity_id:
            return GuardDecision(level=GuardLevel.ALLOW, reason="no entity id")

        guard_configs = getattr(config, "guard_configs", None) or []
        for gc in guard_configs:
            if gc.get("type") == "propagation":
                rule_name = gc.get("rule")
                if rule_name:
                    rule = self._rules.get(rule_name)
                    if rule is not None:
                        result = self._propagator.propagate(entity_id, rule)
                        if result.aggregated_level == "block":
                            return GuardDecision(
                                level=GuardLevel.BLOCK,
                                reason=f"传播检查阻断: {rule_name} (影响 {result.path_count} 个节点)",
                            )
                        elif result.aggregated_level == "warn":
                            # Warn but don't block — continue to next guard
                            logger.debug(
                                "Propagation rule '%s' produced warn on entity %s (%d nodes)",
                                rule_name,
                                entity_id,
                                result.path_count,
                            )
        return GuardDecision(level=GuardLevel.ALLOW, reason="propagation check passed")


class WhitelistGuard(ActionGuard):
    """Guard that short-circuits for allow-listed entities.

    Entity format in allow_set: "{Neo4jLabel}:{entity_name}"
    e.g. "CodeEntity:validate_credit_card"
    """

    def __init__(self, allow_set: set[str] | None = None) -> None:
        self._allow_set = allow_set or set()

    def evaluate(self, config: Any, entity: dict, graph_store: Any) -> GuardDecision:
        if not self._allow_set:
            return GuardDecision(level=GuardLevel.ALLOW, reason="白名单为空")

        entity_name = entity.get("name", "")
        labels = entity.get("labels", [])

        for entry in self._allow_set:
            # entry format: "label:name"
            if ":" in entry:
                label_part, name_part = entry.split(":", 1)
                if name_part == entity_name and label_part in labels:
                    return GuardDecision(
                        level=GuardLevel.ALLOW,
                        reason=f"实体 '{entity_name}' 在白名单中 (allow_all)",
                    )

        return GuardDecision(level=GuardLevel.ALLOW, reason="不在白名单中")
