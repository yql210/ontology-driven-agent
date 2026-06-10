# Phase A 设计方案：V3.4 Agent 驱动四层架构实施

## 背景

V3.3（五层数据驱动闭环，Claude Code 审核 95 分）经反思后被否定：
- LayerKG 的数据是手动 build 的静态数据，不是实时流
- 所有操作都是用户主动发起的，没有自动触发场景
- 规则引擎无数据可监听，闭环走不通

修正为 V3.4（Agent 驱动四层）：
- Agent 同步调 Action，无规则引擎
- 保留 SAGA、TransactionManager、FunctionRunner、Submission Criteria、Connector
- 去掉 EventBus 触发、临时层写入、异步通知

V3.4 设计稿 DESIGN_V34.md 已完成，Claude Code 审核 90/100。

## 现有代码分析

### 当前链路（V1.0）
```
用户输入 → graph.py:run_query()
  → LangGraph ReAct Agent（DeepSeek）
  → LLM 决定调工具
  → tools.py @tool 函数
    ├── 7 个查询工具（保留）
    └── ontology_action → ontology_engine.py → FunctionSelector → 单个 Function
```

### 需要改为（V3.4）
```
用户输入 → graph.py:run_query()（不改）
  → LangGraph ReAct Agent（不改）
  → LLM 决定调工具
  → tools.py @tool 函数
    ├── 7 个查询工具（保留）
    └── express_intent → intent_router.py → action_executor.py
        → Submission Criteria 检查
        → 顺序执行 Function 列表
        → 返回结果
```

### 现有文件清单

| 文件 | 行数 | 用途 | V3.4 处理 |
|------|------|------|----------|
| `agent/tools.py` | 441 | Agent 工具定义 | 改：删 ontology_action，加 express_intent |
| `agent/prompt.py` | 47 | 系统提示词 | 改：从 YAML 动态生成意图说明 |
| `agent/graph.py` | 274 | LangGraph 编排 | 不改 |
| `agent/_helpers.py` | ? | 工具辅助函数 | 小改：加 get_action_executor |
| `ontology_engine.py` | 631 | V1.0 控制层 | 废弃，用 action_executor.py 替代 |
| `ontology_actions.yaml` | 78 | Action 声明（按 entity_type 分组） | 重写：按 intent_type 路由 |
| `actions/code.py` | 360 | Function 实现 | 迁移：改签名 fn(ActionContext) |
| `actions/alert.py` | ? | Alert Function | 迁移：改签名 |

### 现有 Function 签名
```python
def split_large_function(entity_id: str, context: dict, graph_store: Any) -> dict:
```
改为：
```python
@register("check_refactor_eligibility")
def check_refactor_eligibility(ctx: ActionContext) -> FunctionResult:
```

## 设计方案

### 新建文件

| 文件 | 职责 |
|------|------|
| `src/layerkg/intent_handler.py` | 路由 + 执行：ActionConfig、ActionContext、ActionExecutor、intent prompt 生成 |
| `src/layerkg/functions/__init__.py` | 导出 |
| `src/layerkg/functions/registry.py` | @register 装饰器 + FunctionRunner + get_runner |

### 修改文件

| 文件 | 改什么 |
|------|--------|
| `ontology_actions.yaml` | 重写：按 intent_type 路由，加 submission_criteria、trigger_hint |
| `agent/tools.py` | 删 ontology_action，加 express_intent |
| `agent/prompt.py` | 动态生成意图说明 |
| `agent/_helpers.py` | 加 get_action_executor 辅助函数 |
| `actions/code.py` | 迁移 Function 签名 |

### 废弃文件

| 文件 | 处理 |
|------|------|
| `ontology_engine.py` | 不删除，但不再从 tools.py 引用。后续清理 |

### 核心类设计

#### 1. intent_handler.py（路由 + 执行，合并为一个文件）
```python
@dataclass
class ActionConfig:
    intent_type: str
    trigger_hint: str
    bind_to: str  # 实体类型
    submission_criteria: dict  # 结构化条件
    functions: list[str]
    requires_approval: bool

@dataclass
class ActionContext:
    graph_store: Any
    match_data: dict  # {"target": "Cache", "entity": {...}}
    
    def call_function(self, name: str, **kwargs) -> FunctionResult:
        return get_runner().run(name, self, **kwargs)
    
    def unpack_legacy(self) -> tuple[str, dict, Any]:
        """兼容旧签名：返回 (entity_id, context, graph_store)。"""
        entity = self.match_data.get("entity", {})
        return entity.get("id", ""), self.match_data, self.graph_store

@dataclass
class FunctionResult:
    success: bool
    data: dict | None = None
    error: str | None = None

@dataclass
class ActionResult:
    success: bool
    action_name: str
    results: list[FunctionResult]
    summary: str

def load_actions(yaml_path: Path) -> dict[str, ActionConfig]: ...
def build_intent_prompt(actions: dict[str, ActionConfig]) -> str: ...
def resolve_intent(intent_type: str, actions: dict[str, ActionConfig]) -> ActionConfig: ...

class ActionExecutor:
    def __init__(self, graph_store):
        self._graph_store = graph_store
        self._actions = load_actions(YAML_PATH)

    def execute(self, intent_type: str, params: dict) -> ActionResult:
        # 1. resolve_intent → ActionConfig
        # 2. 查图谱：params["target"] → entity node
        # 3. Submission Criteria 检查（结构化）
        # 4. 审批（如果 requires_approval）
        # 5. 构建 ActionContext
        # 6. 顺序执行 functions 列表
        # 7. 返回 ActionResult
```

#### 2. functions/registry.py（注册 + 运行）
```python
_registry: dict[str, Callable] = {}

def register(name: str):
    def decorator(fn):
        _registry[name] = fn
        return fn
    return decorator

def get(name: str) -> Callable | None:
    return _registry.get(name)

class FunctionRunner:
    def run(self, name: str, ctx: ActionContext, **kwargs) -> FunctionResult:
        fn = get(name)
        if fn is None:
            return FunctionResult(success=False, error=f"Function not found: {name}")
        return fn(ctx, **kwargs)

_runner: FunctionRunner | None = None
def get_runner() -> FunctionRunner:
    global _runner
    if _runner is None:
        _runner = FunctionRunner()
    return _runner
```

#### 4. express_intent 工具
```python
@tool
def express_intent(intent_type: str, target: str, params: dict | None = None) -> str:
    """当你识别到用户有操作意图时调用此工具。

    可用操作类型：
    - refactor: 用户要求重构、拆分、优化代码
    - document: 用户要求写文档、补注释
    - analyze_impact: 用户要求分析影响范围
    - diagnose: 用户报告故障、报错
    - notify: 用户要求通知相关人员
    """
    executor = _get_action_executor()
    result = executor.execute(intent_type, {"target": target, **(params or {})})
    return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
```

### ontology_actions.yaml 新格式
```yaml
actions:
  refactor:
    description: "重构代码实体"
    intent_type: refactor
    trigger_hint: "用户要求重构、拆分、优化代码，或说太臃肿/太长/太复杂"
    bind_to: code_entity
    submission_criteria:
      - "entity exists"
      - "entity.lines > 100 or entity.branches > 15"
    functions:
      - check_refactor_eligibility
      - generate_refactor_plan
    requires_approval: false

  document:
    description: "补全代码文档"
    intent_type: document
    trigger_hint: "用户要求写文档、补注释、补全文档"
    bind_to: code_entity
    submission_criteria:
      - "entity exists"
    functions:
      - generate_docs
    requires_approval: false

  analyze_impact:
    description: "分析变更影响"
    intent_type: analyze_impact
    trigger_hint: "用户要求分析影响范围、查看依赖、影响分析"
    bind_to: code_entity
    submission_criteria:
      - "entity exists"
    functions:
      - trace_call_chain
      - find_dependent_modules
    requires_approval: false

  diagnose:
    description: "诊断故障"
    intent_type: diagnose
    trigger_hint: "用户报告故障、报错、服务异常"
    bind_to: alert_entity
    submission_criteria:
      - "entity exists"
    functions:
      - analyze_by_log_pattern
    requires_approval: false

  notify:
    description: "通知相关人员"
    intent_type: notify
    trigger_hint: "用户要求通知、发送告警、告知"
    bind_to: alert_entity
    submission_criteria:
      - "entity exists"
    functions:
      - create_ticket
    requires_approval: false
```

### Submission Criteria（结构化校验，不用 eval）

YAML 里声明式定义条件，代码用 20 行通用校验函数检查：
```yaml
  refactor:
    submission_criteria:
      entity_exists: true
      min_lines: 100
      or:
        - min_lines: 100
        - min_branches: 15
```

```python
def _check_criteria(criteria: dict, entity: dict) -> tuple[bool, str]:
    """结构化校验，不用 eval。"""
    if not entity:
        return False, "目标实体不存在"
    for key, expected in criteria.items():
        if key == "entity_exists":
            continue  # 已在上方检查
        if key == "min_lines":
            actual = entity.get("lines", 0)
            if actual < expected:
                return False, f"行数不足: {actual} < {expected}"
        if key == "min_branches":
            actual = entity.get("branches", 0)
            if actual < expected:
                return False, f"分支数不足: {actual} < {expected}"
        # "or" 条件：任一子条件满足即可
        if key == "or":
            passed = any(_check_criteria(sub, entity)[0] for sub in expected)
            if not passed:
                return False, "不满足任一条件"
    return True, ""
```

### 不改什么

1. `agent/graph.py` — LangGraph 编排不变
2. `agent/trace.py` — Trace 不变
3. 7 个查询工具 — 不变
4. `neo4j_store.py` — 不变
5. `schema.py` — 不变

### 测试计划

| 测试文件 | 测试内容 |
|---------|---------|
| `tests/unit/test_intent_router.py` | YAML 解析、冲突检测、prompt 生成 |
| `tests/unit/test_function_registry.py` | 注册、查询、重复注册报错 |
| `tests/unit/test_action_executor.py` | Criteria 通过/失败、Function 链执行、审批流程 |
| `tests/integration/test_e2e_action.py` | 端到端：用户意图 → Action → 结果 |

### 预估

- 新建文件：5 个（~400 行）
- 修改文件：4 个（~150 行改动）
- 测试文件：4 个（~300 行）
- 总计：~850 行
- Claude Code 执行时间：~2h
