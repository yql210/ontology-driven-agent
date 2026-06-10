"""Adapter to bridge legacy function signatures to the new ActionContext-based signature."""

from __future__ import annotations

from typing import Any

from layerkg.action_types import ActionContext, FunctionResult


def adapt_legacy_function(name: str, legacy_fn: Any):
    """Wrap a legacy-signature function as a new-signature Function.

    Legacy signature: fn(entity_id: str, context: dict, graph_store: Any) -> dict
    New signature: fn(ctx: ActionContext) -> FunctionResult
    """

    def wrapper(ctx: ActionContext) -> FunctionResult:
        entity_id = ctx.match_data.get("entity_id", "")
        try:
            result = legacy_fn(entity_id, ctx.match_data, ctx.graph_store)
        except (ValueError, NotImplementedError) as e:
            return FunctionResult(success=False, error=str(e))
        if isinstance(result, dict):
            return FunctionResult(
                success=result.get("success", True),
                data=result.get("analysis", {}),
                error=result.get("error"),
            )
        return FunctionResult(success=True, data={"raw": result})

    wrapper.__name__ = name
    return wrapper
