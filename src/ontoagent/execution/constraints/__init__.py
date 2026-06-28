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
]
