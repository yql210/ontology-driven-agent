# OntoAgent V3.4 — Agent 驱动四层架构

## 架构总览

```
┌─────────────────────────────────────────┐
│  意图层（Intent）                        │
│  Agent 识别意图 → 直接调用 Action         │
│  YAML 自动生成 prompt                    │
├─────────────────────────────────────────┤
│  控制层（Control）                       │
│  Action = Submission Criteria            │
│         + 审批                           │
│         + SAGA 编排                      │
├─────────────────────────────────────────┤
│  能力层（Capability）                    │
│  通用 Function + 领域 Function           │
│  FunctionRunner（重试/熔断/并行）         │
│  Connector（外部系统接入）               │
├─────────────────────────────────────────┤
│  语义层（Semantic）                      │
│  Schema + GraphStore                     │
│  不改                                    │
└─────────────────────────────────────────┘

规则引擎：不实现。EventBus 保留接口作为未来扩展点。
```

## 核心流程

```
用户: "Cache太臃肿了，帮我重构"

1. Agent 识别意图 → intent_type=refactor
2. Agent 调用 express_intent("refactor", "Cache")
3. express_intent 内部：
   a. 查找 refactor Action 定义（YAML）
   b. 检查 Submission Criteria → Cache 存在 AND lines>100 ✅
   c. 执行 Function（同步，Agent 等待结果）
   d. 返回结果给 Agent
4. Agent 回复用户："重构完成，已通知下游 8 个负责人"
```

**和 V3.3 的区别：没有事件循环，没有规则引擎，Agent 直接同步调用 Action。**

## 层间约束

- 每层只依赖下一层，不跨层不反向
- Action 只引用 Function 名，不引用实现
- Function 通过 graph_store 操作语义层，通过 Connector 访问外部系统
- Connector 只搬运数据，不包含业务逻辑

---

## 一、意图层

### 1.1 Agent 工具设计

保留现有 7 个查询工具，新增 2 个操作工具：

```python
# 查询工具（已有，不变）
query_entity          # 查实体属性
query_relation        # 查关系
query_code_structure  # 查代码结构
search_similar        # 语义搜索
query_change_impact   # 变更影响分析
query_statistics      # 统计信息
query_path            # 路径查询

# 操作工具（新增）
express_intent        # 表达操作意图 → 触发 Action
query_status          # 查询 Action 执行结果
```

### 1.2 express_intent 实现（意图层只做路由）

```python
def express_intent(intent_type: str, target: str, params: dict | None = None) -> dict:
    """Agent 调用此工具表达操作意图。意图层只做路由，执行交给 ActionExecutor。
    
    Args:
        intent_type: 意图类型（从 YAML 自动生成的枚举）
        target: 目标实体名称
        params: 额外参数
    
    Returns:
        Action 执行结果
    """
    # 意图层职责：查找 Action 定义
    action_def = action_registry.get(intent_type)
    if not action_def:
        return {"success": False, "error": f"Unknown intent: {intent_type}"}
    
    # 交给控制层执行
    return action_executor.execute(action_def, target, params or {})
```

### 1.3 ActionExecutor（控制层）

```python
class ActionExecutor:
    """控制层核心：接收意图层的请求，执行 Action。"""
    
    def __init__(self, graph_store, function_runner, approval_manager=None):
        self._graph_store = graph_store
        self._runner = function_runner
        self._approval = approval_manager
    
    def execute(self, action_def: ActionDefinition, target: str, params: dict) -> dict:
        # 1. Submission Criteria 检查
        if action_def.submission_criteria:
            check = action_def.submission_criteria(target, self._graph_store)
            if not check.passed:
                return {"success": False, "error": check.reason}
        
        # 2. 审批检查（如果需要）
        if action_def.requires_approval and self._approval:
            approval = self._approval.request(action_def.name, target, params)
            if not approval.granted:
                return {"success": False, "error": "Approval denied: " + approval.reason}
        
        # 3. 构建 ActionContext（注入 graph_store）
        ctx = ActionContext(
            target=target,
            params=params,
            graph_store=self._graph_store,
            function_runner=self._runner,
        )
        
        # 4. 同步执行 Function
        result = self._runner.run(action_def.function_name, ctx)
        return {"success": result.success, "data": result.data}
```

### 1.4 ActionContext（依赖注入）

```python
@dataclass
class ActionContext:
    """Function 执行上下文，注入所有依赖。"""
    target: str
    params: dict
    graph_store: "GraphStore"        # 语义层访问
    function_runner: "FunctionRunner"  # Function 调用
    
    def call_function(self, name: str, **kwargs) -> "FunctionResult":
        """Function 内部调用其他 Function。"""
        return self.function_runner.run(name, self._replace_target(**kwargs))
    
    def _replace_target(self, **kwargs):
        kwargs.setdefault("graph_store", self.graph_store)
        return kwargs
```

### 1.3 意图路由（YAML → prompt 自动生成）

```yaml
# ontology_actions.yaml
actions:
  refactor:
    description: "重构代码实体"
    intent_type: refactor
    trigger_hint: "用户要求重构、拆分、优化代码，或说代码太臃肿/太长/太复杂"
    submission_criteria: "target存在 AND (lines > 100 OR branches > 15)"
    function: execute_refactor
    requires_approval: false
    
  diagnose:
    description: "诊断问题"
    intent_type: diagnose
    trigger_hint: "用户报告错误、故障、异常、报错"
    submission_criteria: "target存在"
    function: execute_diagnose
    requires_approval: false
    
  notify:
    description: "通知相关人员"
    intent_type: notify
    trigger_hint: "用户要求通知、告知、提醒相关人员"
    submission_criteria: "target存在 AND 有下游依赖"
    function: execute_notify
    requires_approval: false
```

```python
def build_intent_prompt(config: dict) -> str:
    """从 YAML 自动生成 Agent prompt 中的意图路由部分。"""
    lines = ["你可以执行以下操作："]
    for name, action_def in config.get("actions", {}).items():
        lines.append(f"- {action_def['intent_type']}: {action_def['description']}")
        lines.append(f"  触发条件: {action_def['trigger_hint']}")
    return "\n".join(lines)
```

---

## 二、控制层

### 2.1 Action 定义

```python
@dataclass
class ActionDefinition:
    name: str
    description: str
    intent_type: str
    trigger_hint: str
    function_name: str
    submission_criteria: Callable[[str, "GraphStore"], CriteriaResult] | None = None
    requires_approval: bool = False
    approval_timeout: float = 3600  # 审批超时（秒）
```

### 2.2 Submission Criteria

```python
@dataclass
class CriteriaResult:
    passed: bool
    reason: str = ""

def check_refactor_criteria(target: str, graph_store) -> CriteriaResult:
    entity = graph_store.get_node("CodeEntity", target)
    if not entity:
        return CriteriaResult(passed=False, reason=f"{target} 不存在")
    if entity.get("lines", 0) <= 100 and entity.get("branches", 0) <= 15:
        return CriteriaResult(passed=False, reason=f"{target} 复杂度不满足重构条件")
    return CriteriaResult(passed=True)
```

### 2.3 TransactionManager（从 V3.3 保留）

```python
class TransactionManager:
    """Neo4j 原生事务管理器（同步）。"""
    
    def __init__(self, driver):
        self._driver = driver
    
    def run_atomic(self, operations: list[dict]) -> list[dict]:
        """一个事务内多步写操作，失败自动回滚。"""
        with self._driver.session() as session:
            tx = session.begin_transaction()
            results = []
            try:
                for op in operations:
                    result = tx.run(op["cypher"], op.get("params", {}))
                    results.append([r.data() for r in result])
                tx.commit()
                return results
            except Exception:
                tx.rollback()
                raise

class Neo4jTransaction:
    """事务包装器，供 Function 使用。白名单防注入。"""
    
    def __init__(self, tx):
        self._tx = tx
        from layerkg.schema import VALID_ENTITY_LABELS, RELATION_CONSTRAINTS
        self._valid_labels = set(VALID_ENTITY_LABELS) | {
            "TempRequest", "TempDiagnosis", "SagaExecution",
        }
        self._valid_rels = set(RELATION_CONSTRAINTS.keys())
    
    def create_entity(self, label: str, properties: dict):
        if label not in self._valid_labels:
            raise ValueError(f"Invalid label: {label}")
        return self._tx.run(f"CREATE (n:`{label}` $props) RETURN n", {"props": properties})
    
    def create_relation(self, from_id: str, to_id: str, rel_type: str, properties: dict | None = None):
        if rel_type not in self._valid_rels:
            raise ValueError(f"Invalid relation type: {rel_type}")
        params = {"from": from_id, "to": to_id}
        if properties:
            params["props"] = properties
            return self._tx.run(
                f"MATCH (a {{id: $from}}), (b {{id: $to}}) CREATE (a)-[r:`{rel_type}` $props]->(b) RETURN r",
                params)
        return self._tx.run(
            f"MATCH (a {{id: $from}}), (b {{id: $to}}) CREATE (a)-[r:`{rel_type}`]->(b) RETURN r",
            params)
```

### 2.4 SAGA 编排（从 V3.3 保留，同步）

```python
# SagaStep、SagaExecution、SagaOrchestrator 全部同步
# 保留 V3.3 的：持久化、补偿、状态覆盖 guard、Literal 状态枚举
# 唯一改动：全部去掉 async/await，匹配同步 Neo4j driver
```

---

## 三、能力层

### 3.1 Function 注册表

```python
_function_registry: dict[str, Callable] = {}

def register_function(name: str):
    """装饰器：注册 Function。"""
    def decorator(fn):
        _function_registry[name] = fn
        return fn
    return decorator

@register_function("query_entity")
def query_entity_fn(target: str, graph_store=None, **kwargs):
    entity = graph_store.get_node(target)
    return FunctionResult(success=True, data=entity)
```

### 3.2 FunctionRunner（同步版）

```python
class FunctionRunner:
    """Function 执行器（同步）。重试、熔断、并发控制。"""
    
    def __init__(self, graph_store, connector_registry=None):
        self._registry = _function_registry
        self._graph_store = graph_store
        self._connectors = connector_registry or ConnectorRegistry()
        self._policies: dict[str, ExecutionPolicy] = {}
        self._breakers: dict[str, CircuitBreaker] = {}
        self._semaphores: dict[str, threading.Semaphore] = {}
    
    def run(self, func_name: str, ctx: ActionContext, **kwargs) -> FunctionResult:
        fn = self._registry.get(func_name)
        if not fn:
            return FunctionResult(success=False, data={"error": f"Unknown function: {func_name}"})
        
        policy = self._policies.get(func_name, ExecutionPolicy())
        breaker = self._breakers.setdefault(func_name, CircuitBreaker())
        
        if breaker.is_open:
            return FunctionResult(success=False, data={"error": "Circuit breaker open"})
        
        for attempt in range(policy.max_retries + 1):
            try:
                result = fn(ctx, **kwargs)
                breaker.record_success()
                return result
            except Exception as e:
                if attempt < policy.max_retries:
                    time.sleep(policy.retry_delay * (2 ** attempt))
                else:
                    breaker.record_failure()
                    return FunctionResult(success=False, data={"error": str(e)})
```

```python
@dataclass
class ExecutionPolicy:
    max_retries: int = 2
    retry_delay: float = 1.0
    concurrency_limit: int = 5
    timeout: float = 60.0
    fallback: Callable | None = None
```

```python
class CircuitBreaker:
    def __init__(self, failure_threshold=5, recovery_timeout=30.0):
        self._failures = 0
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._last_failure_time = 0
        self._state = "closed"  # closed / open / half_open
    
    @property
    def is_open(self) -> bool:
        if self._state == "closed":
            return False
        if self._state == "open":
            if time.time() - self._last_failure_time > self._recovery_timeout:
                self._state = "half_open"
                return False
            return True
        return False  # half_open
    
    def record_success(self):
        self._failures = 0
        self._state = "closed"
    
    def record_failure(self):
        self._failures += 1
        self._last_failure_time = time.time()
        if self._failures >= self._failure_threshold:
            self._state = "open"
```

### 3.3 Connector 接口

```python
class Connector(ABC):
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
    _connectors: dict[str, Connector] = {}
    
    def register(self, name: str, connector: Connector):
        self._connectors[name] = connector
    
    def get(self, name: str) -> Connector | None:
        return self._connectors.get(name)
```

### 3.4 通用 Function 清单

| Function | 作用 | 自校验 |
|----------|------|--------|
| query_entity | 查实体属性+关系 | 检查实体存在性 |
| update_entity | 更新实体属性 | 检查属性合法性 |
| create_entity | 创建新实体 | 白名单校验 |
| create_relation | 创建关系 | 白名单+domain/range 校验 |
| check_condition | 条件判断 | 无副作用 |
| send_notification | 发通知 | 检查接收人 |

### 3.5 领域 Function（按需扩展）

| Function | 对接的外部系统 |
|----------|--------------|
| fetch_recent_logs | 日志平台（Loki/ELK） |
| analyze_log_pattern | LLM 分析 |
| trace_call_chain | APM（SkyWalking） |
| call_git_api | Git 平台 API |

---

## 四、语义层

**不改。** 保留现有的：
- schema.py（9 实体 15 关系）
- neo4j_store.py（GraphStore 实现）
- chroma_store.py（向量存储）

---

## 五、完整链路

```
用户: "Cache太臃肿了，帮我重构"

1. Agent 收到消息 → prompt 中有 trigger_hint 列表
2. Agent 匹配到 refactor → 调 express_intent("refactor", "Cache")
3. express_intent:
   a. 查找 refactor Action → 找到
   b. Submission Criteria → Cache 存在, lines=320>100 ✅
   c. function_runner.run("execute_refactor", {"target": "Cache"})
4. execute_refactor Function:
   a. query_entity(Cache) → 读图谱
   b. analyze_complexity(Cache) → 计算复杂度
   c. TransactionManager → 创建 ChangeSetEntity + 建立 AFFECTS 关系
   d. send_notification(affected_owners) → 通知下游
5. 结果返回 Agent → 回复用户
```

**全程同步，Agent 等待结果，用户体验：说完话直接拿结果。**

---

## 六、扩展性

| 想做什么 | 改哪层 | 怎么改 |
|---------|--------|--------|
| 新增场景 | 控制层 | YAML 加 Action 声明 |
| 新增外部数据源 | 能力层 | 写 Connector 实现 |
| 新增分析能力 | 能力层 | 写领域 Function + 装饰器 |
| 新增实体类型 | 语义层 | schema.py 加定义 |
| 换外部系统 | 能力层 | 换 Connector 实现 |

---

## 七、实施路线

| 阶段 | 做什么 | 预估 |
|------|--------|------|
| A | Function 注册表 + FunctionRunner + 1 个 Action 跑通 | 3-5h |
| B | TransactionManager + SAGA + Submission Criteria | 3-5h |
| C | 意图路由 + Agent 工具重构 | 2-3h |
| D | Connector + 领域 Function | 2-3h |

---

## 八、和 V3.3 的差异

| 项目 | V3.3 | V3.4 |
|------|------|------|
| 架构 | 五层 | 四层 |
| 驱动方式 | 数据驱动闭环 | Agent 驱动 |
| 规则引擎 | 必须实现 | 不实现（预留接口） |
| 触发方式 | 事件→规则→Action | Agent 直接调 Action |
| Agent 角色 | 意图转化器（写临时层） | 意图识别+直接执行 |
| 事件注入 | neo4j_store 7个注入点 | 不需要 |
| 临时层 | TempRequest/TempDiagnosis | 不需要 |
| Action 执行 | 异步后台 | 同步等待 |
| SAGA | ✅ 保留 | ✅ 保留 |
| TransactionManager | ✅ 保留 | ✅ 保留 |
| FunctionRunner | ✅ 保留 | ✅ 保留 |
| Submission Criteria | ✅ 保留 | ✅ 保留 |
| Connector | ✅ 保留 | ✅ 保留 |
