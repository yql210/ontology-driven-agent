"""6 general-purpose functions with new ActionContext-based signature."""

from __future__ import annotations

from layerkg.action_types import ActionContext, FunctionResult
from layerkg.functions.registry import get_function, register_function


def query_entity(ctx: ActionContext, **kwargs) -> FunctionResult:
    """从 ctx.match_data 查实体属性+关系。"""
    target = ctx.match_data.get("target") or kwargs.get("target")
    if not target:
        return FunctionResult(success=False, error="No target specified")
    entity = ctx.match_data.get("entity")
    if not entity:
        return FunctionResult(success=False, error=f"Entity '{target}' not found in context")
    return FunctionResult(success=True, data=entity)


def update_entity(ctx: ActionContext, **kwargs) -> FunctionResult:
    """更新实体属性。"""
    entity_id = ctx.match_data.get("entity_id")
    if not entity_id:
        return FunctionResult(success=False, error="No entity_id in context")
    properties = kwargs.get("properties", {})
    if not properties:
        return FunctionResult(success=False, error="No properties to update")
    return FunctionResult(success=True, data={"updated": entity_id, "properties": list(properties.keys())})


def create_entity(ctx: ActionContext, **kwargs) -> FunctionResult:
    """创建新实体（白名单校验）。"""
    label = kwargs.get("label")
    if not label:
        return FunctionResult(success=False, error="No label specified")
    from layerkg.schema import VALID_ENTITY_LABELS

    if label not in VALID_ENTITY_LABELS:
        return FunctionResult(success=False, error=f"Invalid label: {label}")
    properties = kwargs.get("properties", {})
    return FunctionResult(success=True, data={"created": label, "properties": properties})


def create_relation(ctx: ActionContext, **kwargs) -> FunctionResult:
    """创建关系。"""
    rel_type = kwargs.get("rel_type")
    from_id = kwargs.get("from_id") or ctx.match_data.get("entity_id")
    to_id = kwargs.get("to_id")
    if not rel_type or not from_id or not to_id:
        return FunctionResult(success=False, error="Missing rel_type, from_id or to_id")
    return FunctionResult(success=True, data={"created_relation": rel_type, "from": from_id, "to": to_id})


def check_condition(ctx: ActionContext, **kwargs) -> FunctionResult:
    """条件判断（无副作用）。"""
    field = kwargs.get("field", "")
    operator = kwargs.get("operator", "==")
    value = kwargs.get("value")
    entity = ctx.match_data.get("entity", {})
    actual = entity.get(field)
    if actual is None:
        return FunctionResult(success=False, error=f"Field '{field}' not found")

    ops = {
        "==": lambda a, b: a == b,
        ">": lambda a, b: a > b,
        ">=": lambda a, b: a >= b,
        "<": lambda a, b: a < b,
        "!=": lambda a, b: a != b,
    }
    op_fn = ops.get(operator)
    if op_fn is None:
        return FunctionResult(success=False, error=f"Unsupported operator: {operator}")
    passed = op_fn(actual, value)

    return FunctionResult(
        success=True,
        data={"condition_met": passed, "field": field, "actual": actual, "expected": value},
    )


def send_notification(ctx: ActionContext, **kwargs) -> FunctionResult:
    """发通知（检查接收人）。"""
    recipients = kwargs.get("recipients", [])
    if not recipients:
        return FunctionResult(success=False, error="No recipients specified")
    message = kwargs.get("message", "")
    return FunctionResult(success=True, data={"notified": recipients, "message_preview": message[:100]})


_GENERAL_FUNCTIONS = {
    "query_entity": query_entity,
    "update_entity": update_entity,
    "create_entity": create_entity,
    "create_relation": create_relation,
    "check_condition": check_condition,
    "send_notification": send_notification,
}


def register_all() -> None:
    """Register all general functions (idempotent, safe after clear_registry)."""
    for name, fn in _GENERAL_FUNCTIONS.items():
        if get_function(name) is None:
            register_function(name)(fn)


register_all()
