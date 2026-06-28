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

    Usage:
        pipeline = ActionGuardPipeline([
            EntityExistsGuard(),
            EntityPropertyGuard(),
            OntologyTraversalGuard(engine),
        ])
        block_reason = pipeline.check(config, entity, graph_store)
        if block_reason:
            return ActionResult(success=False, error=block_reason)
    """

    def __init__(self, guards: list[ActionGuard]) -> None:
        self._guards = guards

    @property
    def guards(self) -> list[ActionGuard]:
        return self._guards

    def check(self, config: Any, entity: dict, graph_store: Any) -> str | None:
        """Execute the guard chain.

        Returns:
            None if chain passes, or a str block reason if any guard blocked.
        """
        for guard in self._guards:
            decision = guard.evaluate(config, entity, graph_store)
            if decision.level == GuardLevel.BLOCK:
                return decision.reason
            # WARN and ALLOW continue to next guard
        return None
