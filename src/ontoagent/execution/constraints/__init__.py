# ontoagent.execution.constraints — constraint evaluation framework

from ontoagent.execution.constraints.engine import ConstraintEngine
from ontoagent.execution.constraints.guard_pipeline import ActionGuard, ActionGuardPipeline
from ontoagent.execution.constraints.guards import (
    EntityExistsGuard,
    EntityPropertyGuard,
    OntologyPropagationGuard,
    OntologyTraversalGuard,
)
from ontoagent.execution.constraints.propagator import ConstraintPropagator, PropagationResult, PropagationRule

# Shared aggregation utility used by both ConstraintEngine and ConstraintPropagator
_AGGREGATION_PRIORITY: dict[str, int] = {"block": 3, "warn": 2, "allow": 1, "": 0}


def aggregate_levels(
    levels: list[str],
    aggregation: str = "max",
) -> str:
    """Aggregate a list of string constraint levels (block/warn/allow) into a single level.

    Args:
        levels: List of constraint level strings.
        aggregation: Aggregation strategy — "max" (most severe), "min" (least severe), "exists" (warn if any).

    Returns:
        Aggregated level string.
    """
    if not levels:
        return "allow"

    if aggregation == "exists":
        return "warn" if any(lvl != "allow" for lvl in levels) else "allow"

    if aggregation == "min":
        return min(levels, key=lambda l: _AGGREGATION_PRIORITY.get(l, 0))

    return max(levels, key=lambda l: _AGGREGATION_PRIORITY.get(l, 0))


__all__ = [
    "ActionGuard",
    "ActionGuardPipeline",
    "ConstraintEngine",
    "ConstraintPropagator",
    "EntityExistsGuard",
    "EntityPropertyGuard",
    "OntologyPropagationGuard",
    "OntologyTraversalGuard",
    "PropagationResult",
    "PropagationRule",
    "aggregate_levels",
]
