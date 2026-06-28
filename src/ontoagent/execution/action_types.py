"""V3.4 Action system public type definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FunctionResult:
    """Function execution result."""

    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class ActionContext:
    """Function execution context — injects all dependencies."""

    graph_store: Any  # GraphStoreProtocol
    match_data: dict[str, Any] = field(default_factory=dict)

    def call_function(self, name: str, **kwargs: Any) -> FunctionResult:
        from ontoagent.execution.functions.registry import get_function

        fn = get_function(name)
        if fn is None:
            return FunctionResult(success=False, error=f"Function '{name}' not registered")
        return fn(self, **kwargs)


@dataclass
class ActionResult:
    """Action execution result."""

    success: bool
    action_name: str
    results: list[FunctionResult] = field(default_factory=list)
    summary: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "action_name": self.action_name,
            "results": [{"success": r.success, "data": r.data, "error": r.error} for r in self.results],
            "summary": self.summary,
            "error": self.error,
        }


@dataclass
class ActionConfig:
    """Action configuration loaded from YAML."""

    name: str
    intent_type: str
    trigger_hint: str
    bind_to: str
    submission_criteria: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    requires_approval: bool = False
    guard_configs: list[dict] = field(default_factory=list)
