# LayerKG V3.4 阶段 A 实施计划 — Agent 驱动四层架构最小闭环

## 目标
实现 Agent → express_intent → ActionExecutor → Function 链 → 返回结果的完整闭环。
替换现有的 ontology_action + OntologyEngine 旧链路。

## 前置条件
- V3.4 设计稿已通过 Claude Code 审核（90/100）
- 现有代码：tools.py (9个工具), ontology_engine.py, actions/code.py, prompt.py, graph.py

## 不做的事
- 不实现 FunctionRunner（重试/熔断/并发控制）
- 不实现 SAGA 编排器
- 不实现 TransactionManager
- 不实现 Connector
- 不修改 graph.py（LangGraph 框架不变）

---

## Task 1: 新建 FunctionResult + ActionContext 数据结构

文件: `src/layerkg/action_types.py` (新建)

```python
"""V3.4 Action 系统公共类型定义。"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass
class FunctionResult:
    """Function 执行结果。"""
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

@dataclass
class ActionContext:
    """Function 执行上下文，注入所有依赖。"""
    graph_store: Any  # GraphStoreProtocol
    match_data: dict[str, Any] = field(default_factory=dict)

    def call_function(self, name: str, **kwargs: Any) -> FunctionResult:
        from layerkg.functions.registry import get_function
        fn = get_function(name)
        if fn is None:
            return FunctionResult(success=False, error=f"Function '{name}' not registered")
        return fn(self, **kwargs)

@dataclass
class ActionResult:
    """Action 执行结果。"""
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
    """从 YAML 加载的 Action 配置。"""
    name: str
    intent_type: str
    trigger_hint: str
    bind_to: str
    submission_criteria: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    requires_approval: bool = False
```

测试: `tests/unit/test_action_types.py`
- test_function_result_defaults
- test_action_context_call_function_success
- test_action_context_call_function_not_found
- test_action_result_to_dict
- test_action_config_defaults

---

## Task 2: 新建 Function 注册表

文件: `src/layerkg/functions/__init__.py` (新建)
文件: `src/layerkg/functions/registry.py` (新建)

```python
"""Function 注册表 — 装饰器注册 + 查询。"""
from __future__ import annotations
from typing import Callable
from layerkg.action_types import FunctionResult, ActionContext

_registry: dict[str, Callable[[ActionContext], FunctionResult]] = {}

def register_function(name: str):
    """装饰器：注册 Function 到全局注册表。"""
    def decorator(fn: Callable[[ActionContext], FunctionResult]):
        if name in _registry:
            raise ValueError(f"Function '{name}' already registered")
        _registry[name] = fn
        return fn
    return decorator

def get_function(name: str) -> Callable[[ActionContext], FunctionResult] | None:
    """按名称查询已注册的 Function。"""
    return _registry.get(name)

def list_functions() -> list[str]:
    """列出所有已注册的 Function 名称。"""
    return sorted(_registry.keys())

def clear_registry() -> None:
    """清空注册表（测试用）。"""
    _registry.clear()
```

测试: `tests/unit/test_function_registry.py`
- test_register_and_get
- test_register_duplicate_raises
- test_get_not_found_returns_none
- test_list_functions
- test_clear_registry

---

## Task 3: 迁移现有 Function 到新注册表

文件: `src/layerkg/functions/builtin.py` (新建)

迁移 `actions/code.py` 中的 4 个已实现 Function：
- split_large_function → check_refactor_eligibility (改名更清晰)
- trace_call_chain → trace_call_chain (保持)
- generate_api_doc → generate_api_doc (保持)
- extract_interface → extract_interface (保持)

每个 Function 签名改为：`fn(ctx: ActionContext) -> FunctionResult`

```python
@register_function("check_refactor_eligibility")
def check_refactor_eligibility(ctx: ActionContext) -> FunctionResult:
    entity = ctx.match_data.get("entity")
    if not entity:
        return FunctionResult(success=False, error="No entity in match_data")
    total_lines = entity.get("lines", 0)
    max_lines = ctx.match_data.get("max_lines", 100)
    if total_lines <= max_lines:
        return FunctionResult(success=False, error=f"只有 {total_lines} 行，不超过 {max_lines}，不需要重构")
    # ...分析逻辑...
    return FunctionResult(success=True, data={...})
```

测试: `tests/unit/test_builtin_functions.py`
- test_check_refactor_eligibility_success
- test_check_refactor_eligibility_too_small
- test_check_refactor_eligibility_no_entity
- test_trace_call_chain_success
- test_trace_call_chain_no_entity
- test_generate_api_doc_success
- test_extract_interface_success

---

## Task 4: 新建意图路由

文件: `src/layerkg/intent_router.py` (新建)

```python
"""意图路由 — 从 YAML 配置构建 intent_type → ActionConfig 映射。"""
from __future__ import annotations
from pathlib import Path
import yaml
from layerkg.action_types import ActionConfig

def build_intent_map(yaml_path: Path) -> dict[str, ActionConfig]:
    """解析 YAML，返回 {intent_type: ActionConfig}。"""
    with yaml_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    
    intent_map: dict[str, ActionConfig] = {}
    for action_name, action_data in data.get("actions", {}).items():
        config = ActionConfig(
            name=action_name,
            intent_type=action_data.get("intent_type", action_name),
            trigger_hint=action_data.get("trigger_hint", ""),
            bind_to=action_data.get("bind_to", ""),
            submission_criteria=action_data.get("submission_criteria", []),
            functions=action_data.get("functions", []),
            requires_approval=action_data.get("requires_approval", False),
        )
        # 冲突检测
        if config.intent_type in intent_map:
            raise ValueError(f"Duplicate intent_type: {config.intent_type}")
        intent_map[config.intent_type] = config
    
    return intent_map

def build_intent_prompt(intent_map: dict[str, ActionConfig]) -> str:
    """生成 Agent prompt 中的意图路由说明。"""
    if not intent_map:
        return ""
    
    lines = ["当用户有操作意图时，使用 express_intent 工具，可用操作："]
    for intent_type, config in intent_map.items():
        lines.append(f"- {intent_type}: {config.trigger_hint}")
    lines.append("参数：intent_type（操作类型）, target（目标实体名称）, params（可选参数 dict）")
    return "\n".join(lines)
```

测试: `tests/unit/test_intent_router.py`
- test_build_intent_map_from_yaml（用 tmp_path 创建临时 YAML）
- test_build_intent_map_duplicate_raises
- test_build_intent_prompt_format
- test_build_intent_prompt_empty

---

## Task 5: 重写 YAML 配置

文件: `src/layerkg/ontology_actions.yaml` (改写)

旧格式（按 entity_type 分组）→ 新格式（按 intent_type 路由）

```yaml
actions:
  refactor:
    intent_type: refactor
    trigger_hint: "用户要求重构、拆分、优化代码，或说太臃肿/太长/太复杂"
    bind_to: code_entity
    submission_criteria:
      - "entity exists"
      - "entity.lines > 100"
    functions:
      - check_refactor_eligibility
    requires_approval: false

  document:
    intent_type: document
    trigger_hint: "用户要求写文档、补注释、补全文档"
    bind_to: code_entity
    submission_criteria:
      - "entity exists"
    functions:
      - generate_api_doc
    requires_approval: false

  analyze_impact:
    intent_type: analyze_impact
    trigger_hint: "用户要求分析影响范围、查看调用链、查看依赖"
    bind_to: code_entity
    submission_criteria:
      - "entity exists"
    functions:
      - trace_call_chain
    requires_approval: false

  extract_interface:
    intent_type: extract_interface
    trigger_hint: "用户要求提取接口、抽象类、解耦"
    bind_to: code_entity
    submission_criteria:
      - "entity exists"
    functions:
      - extract_interface
    requires_approval: false
```

测试: 集成在 Task 4 的 test_build_intent_map_from_yaml 中

---

## Task 6: 新建 ActionExecutor

文件: `src/layerkg/action_executor.py` (新建)

```python
"""V3.4 Action 执行器 — 替代旧 OntologyEngine。"""
from __future__ import annotations
from typing import Any
from pathlib import Path

from layerkg.action_types import ActionConfig, ActionContext, ActionResult, FunctionResult
from layerkg.intent_router import build_intent_map

class ActionExecutor:
    def __init__(self, graph_store: Any, yaml_path: Path | None = None):
        self._graph_store = graph_store
        if yaml_path is None:
            yaml_path = Path(__file__).parent / "ontology_actions.yaml"
        self._intent_map = build_intent_map(yaml_path)

    @property
    def intent_map(self) -> dict[str, ActionConfig]:
        return self._intent_map

    def execute(self, intent_type: str, params: dict[str, Any]) -> ActionResult:
        """执行 Action：意图路由 → Criteria 检查 → Function 链。"""
        # 1. 意图路由
        config = self._intent_map.get(intent_type)
        if config is None:
            return ActionResult(
                success=False, action_name=intent_type,
                error=f"未知操作类型: {intent_type}"
            )

        # 2. 查找目标实体
        target = params.get("target", "")
        entity = self._resolve_entity(target)
        if entity is None:
            return ActionResult(
                success=False, action_name=config.name,
                error=f"未找到实体 '{target}'"
            )

        # 3. Submission Criteria 检查
        criteria_error = self._check_criteria(config, entity)
        if criteria_error:
            return ActionResult(
                success=False, action_name=config.name,
                error=criteria_error
            )

        # 4. 构建 ActionContext
        ctx = ActionContext(
            graph_store=self._graph_store,
            match_data={**params, "entity": entity, "entity_id": entity.get("id", "")},
        )

        # 5. 顺序执行 Function 链
        results: list[FunctionResult] = []
        for func_name in config.functions:
            result = ctx.call_function(func_name)
            results.append(result)
            if not result.success:
                return ActionResult(
                    success=False, action_name=config.name,
                    results=results, error=f"Function '{func_name}' failed: {result.error}"
                )

        # 6. 成功
        return ActionResult(
            success=True, action_name=config.name, results=results,
            summary=f"操作 '{config.name}' 执行成功"
        )

    def _resolve_entity(self, target: str) -> dict | None:
        if not target:
            return None
        cypher = "MATCH (n {name: $name}) RETURN n.id AS id, n.name AS name, n.lines AS lines, n.branches AS branches, n.entityType AS entityType, labels(n) AS labels LIMIT 1"
        results = self._graph_store.query(cypher, {"name": target})
        if not results:
            # 模糊匹配
            cypher = "MATCH (n) WHERE n.name CONTAINS $name RETURN n.id AS id, n.name AS name, n.lines AS lines, n.branches AS branches, n.entityType AS entityType, labels(n) AS labels LIMIT 1"
            results = self._graph_store.query(cypher, {"name": target})
        return results[0] if results else None

    def _check_criteria(self, config: ActionConfig, entity: dict) -> str | None:
        """检查 Submission Criteria，返回 None 表示通过，否则返回错误信息。"""
        for criterion in config.submission_criteria:
            if criterion == "entity exists":
                if not entity:
                    return "目标实体不存在"
            elif criterion.startswith("entity."):
                # 解析 "entity.lines > 100"
                field_expr = criterion[7:]  # 去掉 "entity."
                try:
                    field, op, value = field_expr.split()
                    actual = entity.get(field)
                    if actual is not None:
                        if op == ">" and not (actual > int(value)):
                            return f"不满足条件: {field}({actual}) <= {value}"
                        elif op == ">=" and not (actual >= int(value)):
                            return f"不满足条件: {field}({actual}) < {value}"
                except (ValueError, TypeError):
                    pass  # 解析失败的条件跳过
        return None
```

测试: `tests/unit/test_action_executor.py`
- test_execute_refactor_success（mock graph_store 返回 lines=320 的实体）
- test_execute_refactor_criteria_fail（lines=50 不满足 > 100）
- test_execute_unknown_intent
- test_execute_entity_not_found
- test_execute_function_chain_success
- test_execute_function_chain_stops_on_failure

---

## Task 7: 改写 Agent 工具 — express_intent

文件: `src/layerkg/agent/tools.py` (修改)

改动：
1. 删除 `ontology_action` 工具
2. 新增 `express_intent` 工具
3. ALL_TOOLS 列表更新

```python
@tool
def express_intent(intent_type: str, target: str, params: dict | None = None) -> str:
    """当你识别到用户有操作意图时调用此工具。可用操作类型会在系统提示中列出。
    
    Args:
        intent_type: 操作类型（如 refactor, document, analyze_impact）
        target: 目标实体名称
        params: 可选参数（dict）
    
    Returns:
        执行结果的 JSON 格式字符串
    """
    try:
        from layerkg.action_executor import ActionExecutor
        neo4j = get_neo4j()
        executor = _get_action_executor(neo4j)
        result = executor.execute(intent_type, {"target": target, **(params or {})})
        return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": f"操作执行失败: {e!s}"}, ensure_ascii=False)

_action_executor: ActionExecutor | None = None

def _get_action_executor(graph_store):
    global _action_executor
    if _action_executor is None:
        from layerkg.action_executor import ActionExecutor
        _action_executor = ActionExecutor(graph_store)
    return _action_executor

ALL_TOOLS = [
    semantic_search, graph_query, impact_analysis, get_context,
    list_concepts, get_module_tree, detect_changes, export_graph,
    express_intent,  # 替换 ontology_action
]
```

测试: `tests/unit/test_tools_express_intent.py`
- test_express_intent_refactor（mock ActionExecutor）
- test_express_intent_unknown_type
- test_express_intent_exception_handling

---

## Task 8: 更新 Agent prompt

文件: `src/layerkg/agent/prompt.py` (修改)

改动：
1. 删除硬编码的 ontology_action 说明
2. 从 intent_router 动态生成意图路由文本

```python
from layerkg.intent_router import build_intent_map, build_intent_prompt
from pathlib import Path

_yaml_path = Path(__file__).parent.parent / "ontology_actions.yaml"
_intent_map = build_intent_map(_yaml_path)
INTENT_SECTION = build_intent_prompt(_intent_map)

AGENT_SYSTEM_PROMPT = f"""你是 LayerKG 代码知识图谱助手，帮助用户理解代码架构、查询依赖关系、分析变更影响。

## 工具速查

| 工具 | 用途 | 关键参数 |
|------|------|----------|
| get_context | 查实体详情（属性+关系+相似实体） | entity_name(必填) |
| impact_analysis | 变更影响范围分析 | entity_name(必填), depth(默认3) |
| graph_query | 自定义 Cypher 查询 | cypher(必填) |
| semantic_search | 语义搜索代码片段 | query(必填), top_k(默认5) |
| express_intent | 执行操作（重构/文档/分析等） | intent_type, target, params |
| detect_changes | 检测 Git 代码变更 | since(默认HEAD~1) |
| list_concepts | 列出概念实体（可能为空） | 无 |
| get_module_tree | 模块结构树（可能为空） | 无 |
| export_graph | 导出可视化数据 | limit(默认100) |

### 操作类意图
{INTENT_SECTION}

## Schema（9 实体 15 关系）

实体: CodeEntity, ConceptEntity, DocEntity, ResourceEntity, ModuleEntity, ChangeSetEntity, LogEntity, AlertEntity, ServiceEntity

关系:
- 结构: CALLS, EXTENDS, IMPLEMENTS, IMPORTS, CONTAINS
- 语义: SEMANTIC_IMPACT, DESCRIBES, ILLUSTRATES, DERIVED_FROM
- 变更: CHANGED_IN, AFFECTS
- 运维: TRIGGERED_BY, LOGS_FROM, RUNS_AS, SERVICE_DEPENDS_ON

## 数据现状
当前图谱以 CodeEntity 为主。ConceptEntity、ModuleEntity 等是否为空取决于构建配置。优先用 CodeEntity 查询。

## 规则
1. 必须调用工具获取数据，不能凭记忆回答
2. 查询类优先用专用工具，graph_query 作为兜底
3. 操作类用 express_intent 工具
4. 工具返回空或 error 时，换一个工具尝试一次，仍然失败则直接告知用户，不要重试
5. 所有 Cypher 查询必须加 LIMIT，禁止全表扫描
"""
```

测试: `tests/unit/test_prompt_update.py`
- test_prompt_contains_express_intent
- test_prompt_contains_intent_section
- test_prompt_not_contains_ontology_action

---

## Task 9: 端到端集成测试

文件: `tests/integration/test_e2e_action_v34.py`

用 mock graph_store 测试完整链路：

```python
class MockGraphStore:
    def __init__(self, entities: dict):
        self._entities = entities
    
    def get_node(self, node_id):
        return self._entities.get(node_id)
    
    def query(self, cypher, params=None):
        name = (params or {}).get("name", "")
        for eid, ent in self._entities.items():
            if ent.get("name") == name:
                return [{"id": eid, "name": ent["name"], "lines": ent.get("lines", 0), 
                         "branches": ent.get("branches", 0), "entityType": ent.get("entityType", ""),
                         "labels": ent.get("labels", [])}]
        # 模糊匹配
        for eid, ent in self._entities.items():
            if name in ent.get("name", ""):
                return [{"id": eid, "name": ent["name"], "lines": ent.get("lines", 0),
                         "branches": ent.get("branches", 0), "entityType": ent.get("entityType", ""),
                         "labels": ent.get("labels", [])}]
        return []
```

测试场景：
- test_e2e_refactor_success: Cache(lines=320) → refactor → 返回重构建议
- test_e2e_refactor_too_small: Small(lines=50) → refactor → 不满足条件
- test_e2e_document_success: Cache → document → 返回文档
- test_e2e_analyze_impact: Cache → analyze_impact → 返回调用链
- test_e2e_entity_not_found: NotFound → refactor → 实体不存在
- test_e2e_unknown_intent: unknown → 返回未知操作

---

## Task 10: 清理旧代码 + 验证

1. `src/layerkg/ontology_engine.py` — 标记为 deprecated，保留文件但加 DeprecationWarning（不删除，避免破坏现有测试）
2. `src/layerkg/actions/code.py` — 保留旧文件（旧测试还在用），新 Function 在 functions/builtin.py
3. 运行全量测试：`uv run pytest tests/ -v`
4. 运行 ruff：`uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/`

---

## YAML 迁移映射表（Task 5 验收条件）

| 旧 entity_type | 旧 action | 新 intent_type | 新 Function |
|----------------|-----------|---------------|-------------|
| code_entity | refactor | refactor | check_refactor_eligibility |
| code_entity | document | document | generate_api_doc |
| code_entity | analyze_impact | analyze_impact | trace_call_chain |
| code_entity | delete | _(暂不迁移，需审批)_ | — |
| alert_entity | diagnose | _(暂不迁移)_ | — |
| alert_entity | rollback | _(暂不迁移)_ | — |
| alert_entity | notify | _(暂不迁移)_ | — |

注：Phase A 只迁移 code_entity 下的 3 个无审批 Action。有审批需求的（delete/rollback）和 alert_entity 的留在后续阶段。

## FunctionAdapter 桥接层（Task 3.5）

为兼容旧签名 `fn(entity_id, context, graph_store) -> dict`，加一个适配器：

```python
# src/layerkg/functions/adapter.py
from layerkg.action_types import ActionContext, FunctionResult

def adapt_legacy_function(name: str, legacy_fn):
    """将旧签名 Function 包装为新签名。"""
    def wrapper(ctx: ActionContext) -> FunctionResult:
        entity_id = ctx.match_data.get("entity_id", "")
        result = legacy_fn(entity_id, ctx.match_data, ctx.graph_store)
        if isinstance(result, dict):
            return FunctionResult(
                success=result.get("success", True),
                data=result.get("analysis", {}),
                error=result.get("error"),
            )
        return FunctionResult(success=True, data={"raw": result})
    wrapper.__name__ = name
    return wrapper
```

在 builtin.py 中，未迁移的 Function 用 adapter 包装：

```python
from layerkg.actions.code import split_large_function as _legacy_split
register_function("check_refactor_eligibility")(adapt_legacy_function("check_refactor_eligibility", _legacy_split))
```

## 执行批次

| 批次 | Tasks | 内容 | 卡点 | max-turns |
|------|-------|------|------|-----------|
| Batch 1 | 1-3 | 数据结构 + 注册表 + 内置 Function + FunctionAdapter | 跑通 test_action_types + test_function_registry + test_builtin_functions | 50 |
| Batch 2 | 4-6 | 意图路由 + YAML迁移 + ActionExecutor | 跑通 test_intent_router + test_action_executor | 50 |
| Batch 3 | 7-10 | 工具改写 + prompt + 集成测试 + 清理 | 全量测试 1214+ passed + ruff clean | 50 |

### 批次间卡点验证
```bash
# Batch 1 完成后：
uv run pytest tests/unit/test_action_types.py tests/unit/test_function_registry.py tests/unit/test_builtin_functions.py -v

# Batch 2 完成后：
uv run pytest tests/unit/test_intent_router.py tests/unit/test_action_executor.py -v

# Batch 3 完成后：
uv run pytest tests/ -v --tb=short
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

## 验证标准

- [ ] 所有新测试通过
- [ ] 旧测试不被破坏（1214 passed）
- [ ] ruff check + format clean
- [ ] express_intent 能正确路由到对应 Action
- [ ] Submission Criteria 正确拦截不合格操作
- [ ] Function 链顺序执行，失败时停止
