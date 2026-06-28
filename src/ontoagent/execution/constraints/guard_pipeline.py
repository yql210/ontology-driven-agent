"""Pluggable ActionGuard pipeline — sequential evaluation with early BLOCK return."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ontoagent.domain.schema import GuardDecision, GuardLevel


class ActionGuard(ABC):
    """Pluggable action guard interface.

    Each guard evaluates a single concern and returns a GuardDecision.
    The pipeline respects BLOCK → immediate return, WARN → continue, ALLOW → continue.
    """

    @abstractmethod
    def evaluate(self, config: Any, entity: dict, graph_store: Any) -> GuardDecision:
        """Evaluate this guard against the action context.

        Args:
            config: ActionConfig for the current action.
            entity: Resolved target entity dict (may be empty).
            graph_store: GraphStore instance for graph queries.

        Returns:
            GuardDecision with level (ALLOW/WARN/BLOCK) and reason.
        """
        ...


class ActionGuardPipeline:
    """Guard chain — sequential execution, first BLOCK returns immediately.

    Guards should be ordered cheap-before-expensive:
    (1) EntityExistsGuard (memory lookup)
    (2) EntityPropertyGuard (memory lookup)
    (3) OntologyTraversalGuard (constrained graph query)
    (4) OntologyPropagationGuard (BFS graph traversal, potentially expensive)

    Usage:
        pipeline = ActionGuardPipeline([
            EntityExistsGuard(),
            EntityPropertyGuard(),
            OntologyTraversalGuard(engine),
        ])
        block_reason, warnings = pipeline.check(config, entity, graph_store)
        if block_reason:
            return ActionResult(success=False, error=block_reason)
        # warnings can be surfaced to the user
    """

    def __init__(self, guards: list[ActionGuard]) -> None:
        self._guards = guards

    @property
    def guards(self) -> list[ActionGuard]:
        return self._guards

    def check(self, config: Any, entity: dict, graph_store: Any) -> tuple[str | None, list[str]]:
        """Execute the guard chain.

        Returns:
            Tuple of (block_reason | None, warnings: list[str]).
            block_reason is None if chain passes, or a str if any guard blocked.
            warnings accumulates WARN-level reasons from guards.
        """
        warnings: list[str] = []
        for guard in self._guards:
            decision = guard.evaluate(config, entity, graph_store)
            if decision.level == GuardLevel.BLOCK:
                return decision.reason, warnings
            elif decision.level == GuardLevel.WARN:
                warnings.append(decision.reason)
            # ALLOW continues to next guard
        return None, warnings
