# LayerKG V3.4 Phase D 实施计划 — Connector + 通用 Function

## 目标
1. 实现 Connector 抽象接口 + ConnectorRegistry
2. 实现 6 个通用 Function（新签名，不依赖旧代码）
3. 清理 FunctionAdapter + 旧 actions/code.py 的技术债

## 前置条件
- Phase A-C 完成（express_intent + ActionExecutor + FunctionRunner + SAGA）
- Phase B 完成（TransactionManager + CircuitBreaker）
- 当前 4 个 Function 用 FunctionAdapter 包装旧签名

## 不做的事
- 不实现具体 Connector（Git/Loki 等）— 只实现接口 + 1 个 mock 示例
- 不修改 graph.py / prompt.py / tools.py
- 不修改 YAML 配置

---

## Task 1: Connector 抽象接口 + ConnectorRegistry

文件: `src/layerkg/connectors/__init__.py` (新建)
文件: `src/layerkg/connectors/base.py` (新建)

```python
from abc import ABC, abstractmethod

class Connector(ABC):
    """外部系统接入抽象接口。"""
    
    @abstractmethod
    def fetch(self, params: dict) -> list[dict]:
        """从外部系统拉取数据。"""
    
    @abstractmethod
    def sync(self, graph_store, params: dict | None = None) -> int:
        """批量同步外部数据到图谱，返回同步条数。"""
    
    @abstractmethod
    def push(self, data: dict) -> bool:
        """推送数据到外部系统。"""
    
    @abstractmethod
    def health_check(self) -> bool:
        """健康检查。"""

class ConnectorRegistry:
    """Connector 注册表。"""
    
    def __init__(self):
        self._connectors: dict[str, Connector] = {}
    
    def register(self, name: str, connector: Connector) -> None:
        if name in self._connectors:
            raise ValueError(f"Connector '{name}' already registered")
        self._connectors[name] = connector
    
    def get(self, name: str) -> Connector | None:
        return self._connectors.get(name)
    
    def list_connectors(self) -> list[str]:
        return sorted(self._connectors.keys())
    
    def clear(self) -> None:
        self._connectors.clear()
```

测试: `tests/unit/test_connector_registry.py`
- test_register_and_get
- test_register_duplicate_raises
- test_get_not_found
- test_list_connectors
- test_clear

---

## Task 2: Mock Connector 示例

文件: `src/layerkg/connectors/mock_connector.py` (新建)

```python
class MockConnector(Connector):
    """Mock connector for testing and demonstration."""
    
    def __init__(self):
        self._data: list[dict] = []
        self._pushed: list[dict] = []
    
    def fetch(self, params: dict) -> list[dict]:
        return self._data
    
    def sync(self, graph_store, params: dict | None = None) -> int:
        count = 0
        for item in self._data:
            # 写入图谱逻辑由子类实现
            count += 1
        return count
    
    def push(self, data: dict) -> bool:
        self._pushed.append(data)
        return True
    
    def health_check(self) -> bool:
        return True
    
    def add_mock_data(self, data: list[dict]):
        self._data.extend(data)
```

测试: `tests/unit/test_mock_connector.py`
- test_fetch_returns_data
- test_push_appends
- test_health_check
- test_sync_returns_count

---

## Task 3: 6 个通用 Function（新签名）

文件: `src/layerkg/functions/general.py` (新建)

```python
from layerkg.functions.registry import register_function
from layerkg.action_types import ActionContext, FunctionResult

@register_function("query_entity")
def query_entity(ctx: ActionContext, **kwargs) -> FunctionResult:
    """查实体属性+关系。"""
    target = ctx.match_data.get("target") or kwargs.get("target")
    if not target:
        return FunctionResult(success=False, error="No target specified")
    entity = ctx.match_data.get("entity")
    if not entity:
        return FunctionResult(success=False, error=f"Entity '{target}' not found in context")
    return FunctionResult(success=True, data=entity)

@register_function("update_entity")
def update_entity(ctx: ActionContext, **kwargs) -> FunctionResult:
    """更新实体属性。"""
    entity_id = ctx.match_data.get("entity_id")
    properties = kwargs.get("properties", {})
    if not entity_id:
        return FunctionResult(success=False, error="No entity_id in context")
    if not properties:
        return FunctionResult(success=False, error="No properties to update")
    # 实际更新通过 graph_store
    return FunctionResult(success=True, data={"updated": entity_id, "properties": list(properties.keys())})

@register_function("create_entity")
def create_entity(ctx: ActionContext, **kwargs) -> FunctionResult:
    """创建新实体（白名单校验）。"""
    label = kwargs.get("label")
    properties = kwargs.get("properties", {})
    if not label:
        return FunctionResult(success=False, error="No label specified")
    # 白名单校验
    from layerkg.schema import VALID_ENTITY_LABELS
    if label not in VALID_ENTITY_LABELS:
        return FunctionResult(success=False, error=f"Invalid label: {label}")
    return FunctionResult(success=True, data={"created": label, "properties": properties})

@register_function("create_relation")
def create_relation(ctx: ActionContext, **kwargs) -> FunctionResult:
    """创建关系（白名单校验）。"""
    rel_type = kwargs.get("rel_type")
    from_id = kwargs.get("from_id") or ctx.match_data.get("entity_id")
    to_id = kwargs.get("to_id")
    if not rel_type or not from_id or not to_id:
        return FunctionResult(success=False, error="Missing rel_type, from_id or to_id")
    return FunctionResult(success=True, data={"created_relation": rel_type, "from": from_id, "to": to_id})

@register_function("check_condition")
def check_condition(ctx: ActionContext, **kwargs) -> FunctionResult:
    """条件判断（无副作用）。"""
    condition = kwargs.get("condition", "")
    field = kwargs.get("field", "")
    operator = kwargs.get("operator", "==")
    value = kwargs.get("value")
    entity = ctx.match_data.get("entity", {})
    actual = entity.get(field)
    if actual is None:
        return FunctionResult(success=False, error=f"Field '{field}' not found")
    
    passed = False
    if operator == "==": passed = actual == value
    elif operator == ">": passed = actual > value
    elif operator == ">=": passed = actual >= value
    elif operator == "<": passed = actual < value
    elif operator == "!=": passed = actual != value
    
    return FunctionResult(success=True, data={"condition_met": passed, "field": field, "actual": actual, "expected": value})

@register_function("send_notification")
def send_notification(ctx: ActionContext, **kwargs) -> FunctionResult:
    """发通知（检查接收人）。"""
    recipients = kwargs.get("recipients", [])
    message = kwargs.get("message", "")
    if not recipients:
        return FunctionResult(success=False, error="No recipients specified")
    return FunctionResult(success=True, data={"notified": recipients, "message_preview": message[:100]})
```

测试: `tests/unit/test_general_functions.py`
- test_query_entity_success
- test_query_entity_no_target
- test_query_entity_not_in_context
- test_update_entity_success
- test_update_entity_no_properties
- test_create_entity_success
- test_create_entity_invalid_label
- test_create_relation_success
- test_create_relation_missing_params
- test_check_condition_gt
- test_check_condition_eq
- test_check_condition_field_not_found
- test_send_notification_success
- test_send_notification_no_recipients

---

## Task 4a: 逐个迁移旧 Function 到新签名

逐个迁移 4 个旧 Function，每个迁移后跑测试确认：

1. `split_large_function` → `check_refactor_eligibility`（新签名，直接在 builtin.py 实现）
2. `trace_call_chain` → `trace_call_chain`（新签名）
3. `generate_api_doc` → `generate_api_doc`（新签名）
4. `extract_interface` → `extract_interface`（新签名）

每迁移一个：
- 在 `functions/builtin.py` 中替换 adapt_legacy_function 调用为新签名实现
- 保留核心业务逻辑，只改签名和返回值
- 跑 `tests/unit/test_builtin_functions.py` 确认通过

注意：`actions/code.py` 中的 Function 接受 `entity_id, context, graph_store` 参数，需要改为接受 `ctx: ActionContext`。从 ctx.match_data["entity_id"]、ctx.graph_store 获取等价参数。

## Task 4b: 删除 adapter.py

确认 4 个 Function 全部迁移后：
- 删除 `src/layerkg/functions/adapter.py`
- 删除 `functions/builtin.py` 中对 adapter 的 import
- 跑全量测试确认无引用断裂

## Task 4c: 标记旧文件 deprecated

- `actions/code.py` 和 `ontology_engine.py` 已有 DeprecationWarning
- 确认 warning 信息指向新模块
- 不删除旧文件（开源准备阶段统一清理）

---

## Task 5: 集成测试 + 验证

文件: `tests/integration/test_e2e_phase_d.py`

测试场景：
- test_e2e_general_query_entity: express_intent → ActionExecutor → query_entity
- test_e2e_general_create_entity: create_entity 白名单校验
- test_e2e_general_check_condition: check_condition 条件判断
- test_e2e_connector_registry: 注册 + 查询 connector

验证：
- `uv run pytest tests/ -v` 全量通过
- `uv run ruff check src/ tests/` clean
- `uv run ruff format --check src/ tests/` clean

---

## 执行批次

| 批次 | Tasks | 内容 | 卡点 | max-turns |
|------|-------|------|------|-----------|
| Batch 1 | 1-2 | Connector 接口 + Registry + Mock | test_connector_registry + test_mock_connector | 30 |
| Batch 2 | 3 | 6 个通用 Function | test_general_functions (14个) | 40 |
| Batch 3 | 4-5 | 清理技术债 + 集成测试 | 全量测试 + ruff | 50 |
