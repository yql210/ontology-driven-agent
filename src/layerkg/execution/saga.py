"""SAGA orchestrator for multi-step operations with compensation."""

from __future__ import annotations

import contextlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from layerkg.execution.action_types import ActionContext, ActionResult, FunctionResult


@dataclass
class SagaStep:
    """A single step in a SAGA workflow.

    Attributes:
        name: Human-readable step name.
        action: Forward action executed during normal flow.
        compensation: Optional rollback action executed on failure.
    """

    name: str
    action: Callable[[ActionContext], FunctionResult]
    compensation: Callable[[ActionContext], FunctionResult] | None = None


class SagaExecution:
    """Tracks the state of a single SAGA execution.

    Attributes:
        id: Unique identifier for this execution.
        steps: The steps to execute.
        completed: Indices of successfully completed steps.
        status: Current execution status.
    """

    def __init__(
        self,
        saga_id: str,
        steps: list[SagaStep],
        graph_store: Any | None = None,
    ) -> None:
        self.id = saga_id
        self.steps = steps
        self.completed: list[int] = []
        self.status: Literal["pending", "running", "completed", "compensating", "compensated", "failed"] = "pending"
        self._graph_store = graph_store

    def persist(self) -> None:
        """Persist SAGA state to the graph store."""
        if self._graph_store is not None:
            self._graph_store.merge_node(
                "SagaExecution",
                self.id,
                {"status": self.status, "completed_steps": json.dumps(self.completed)},
            )


class SagaOrchestrator:
    """Executes a list of SAGA steps with automatic compensation on failure."""

    def execute(self, steps: list[SagaStep], ctx: ActionContext) -> ActionResult:
        """Run all *steps* forward; compensate on failure.

        Args:
            steps: Ordered list of SAGA steps.
            ctx: Execution context (may carry a ``graph_store`` for persistence).

        Returns:
            An ``ActionResult`` indicating success or failure.
        """
        graph_store = getattr(ctx, "graph_store", None)
        execution = SagaExecution(
            saga_id=str(uuid.uuid4()),
            steps=steps,
            graph_store=graph_store,
        )
        execution.status = "running"
        execution.persist()

        results: list[FunctionResult] = []
        try:
            for i, step in enumerate(steps):
                result = step.action(ctx)
                results.append(result)
                if not result.success:
                    execution.status = "compensating"
                    execution.persist()
                    self._compensate(execution, ctx)
                    execution.status = "failed"
                    execution.persist()
                    return ActionResult(
                        success=False,
                        action_name="saga",
                        results=results,
                        error=f"Step '{step.name}' failed",
                    )
                execution.completed.append(i)
                execution.persist()

            execution.status = "completed"
            execution.persist()
            return ActionResult(
                success=True,
                action_name="saga",
                results=results,
                summary=f"SAGA completed {len(steps)} steps",
            )
        except Exception as e:
            execution.status = "compensating"
            execution.persist()
            self._compensate(execution, ctx)
            execution.status = "failed"
            execution.persist()
            return ActionResult(
                success=False,
                action_name="saga",
                results=results,
                error=str(e),
            )

    def _compensate(self, execution: SagaExecution, ctx: ActionContext) -> None:
        """Run compensations for completed steps in reverse order.

        Compensation failures are logged but do not interrupt other compensations.
        """
        for idx in reversed(execution.completed):
            step = execution.steps[idx]
            if step.compensation is not None:
                with contextlib.suppress(Exception):
                    step.compensation(ctx)
