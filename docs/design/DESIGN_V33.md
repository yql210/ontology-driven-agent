# LayerKG V3.3 — 解决 Claude Code 审核反馈（72/100 → 目标 85+）

## 修正清单对照

| # | Claude Code 反馈 | 修正方案 |
|---|-----------------|---------|
| 1 | 两份文档术语/分层不一致 | 统一为五层架构，明确定义每层职责和层间约束 |
| 2 | Connector 层完全缺失 | 新增 Connector 抽象接口 + 注册表 + 3个示例实现 |
| 3 | 事件链补偿机制不完整 | 引入 SAGA 模式，每个 Action 记录 compensate 函数 |
| 4 | 事务性写操作手动回滚不可靠 | 用 Neo4j 原生事务（session.begin_transaction） |
| 5 | 框架层缺并行/重试/熔断 | 新增 FunctionRunner + ExecutionPolicy + CircuitBreaker |
| 6 | depth=5 可能不够、无幂等、无背压 | depth 按 Action 可配置 + 事件幂等标记 + 信号量并发控制 |
| 7 | intent_type 固定枚举 | 从 YAML 自动生成，不硬编码 |
| 8 | Schema 身份分裂 | 数据模型和触发规则显式分离 |
| 9 | 迁移风险：neo4j_store 事件发射 | 定义明确的事件发射注入点清单 |

---

## 一、统一五层架构

```
┌──────────────────────────────────────────────┐
│  第1层：意图层（Intent Layer）                 │
│  组件：Agent + IntentRouter                   │
│  职责：自然语言 → intent_type 路由             │
│  扩展：加 Action 时改 YAML，prompt 自动更新     │
├──────────────────────────────────────────────┤
│  第2层：控制层（Control Layer）                │
│  组件：ActionShell + RuleEngine + SagaOrchestrator │
│  职责：触发规则匹配、Submission Criteria 校验、 │
│        审批、SAGA 编排、审计                    │
│  扩展：YAML 声明 + Python 触发函数              │
├──────────────────────────────────────────────┤
│  第3层：能力层（Capability Layer）             │
│  组件：通用 Function + 领域 Function            │
│  职责：业务逻辑执行                            │
│  扩展：写一个 Python 函数 + 装饰器注册           │
├──────────────────────────────────────────────┤
│  第4层：连接层（Connector Layer）              │
│  组件：Connector 抽象接口 + 具体实现            │
│  职责：外部系统数据搬运（认证、协议、格式映射）  │
│  扩展：实现 Connector 接口                      │
├──────────────────────────────────────────────┤
│  第5层：语义层（Semantic Layer）               │
│  组件：Schema + GraphStore + EventBus          │
│  职责：数据模型定义 + 图谱存储 + 事件发射       │
│  子分：数据模型（稳定不变）+ 触发规则（可演进）  │
│  扩展：schema.py 加实体定义                     │
└──────────────────────────────────────────────┘
```

**层间约束（强制）：**
- 每层只依赖下一层，不跨层、不反向依赖
- Action 只引用 Function 名，不引用 Function 实现
- Function 只通过 graph_store 操作语义层，通过 Connector 访问外部系统
- Connector 只负责数据搬运，不包含业务逻辑，不触发 Action
- 语义层的事件发射是唯一的向上通知机制

---

## 二、Connector 层（修正 2）

### 抽象接口

```python
# src/layerkg/connectors/base.py
from abc import ABC, abstractmethod
from typing import Any

class Connector(ABC):
    """外部系统连接器抽象接口。"""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """连接器名称。"""
    
    @abstractmethod
    def health_check(self) -> bool:
        """检查连接是否正常。"""
    
    @abstractmethod
    def fetch(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        """按需拉取外部数据。返回结构化字典列表。"""
    
    @abstractmethod
    def sync(self, graph_store: Any, params: dict[str, Any] | None = None) -> int:
        """定期同步外部数据到图谱。返回同步记录数。不需要同步的返回 0。"""
    
    @abstractmethod
    def push(self, data: dict[str, Any]) -> bool:
        """推送数据到外部系统。不需要推送的返回 True。"""
```

### 注册表

```python
# src/layerkg/connectors/registry.py
_connectors: dict[str, "Connector"] = {}

def register(connector: "Connector") -> None:
    _connectors[connector.name] = connector

def get(name: str) -> "Connector | None":
    return _connectors.get(name)

def health_check_all() -> dict[str, bool]:
    return {name: c.health_check() for name, c in _connectors.items()}
```

### Connector 和 Function 的边界

| | Connector | 领域 Function |
|---|---|---|
| 职责 | 纯数据搬运（认证、协议转换、格式映射） | 业务逻辑（分析、决策、写图谱） |
| 知道图谱 | sync 方法接收 graph_store 参数 | 通过 graph_store 读写图谱 |
| 知道业务 | 不知道 | 知道 |
| 触发 Action | 不能 | 通过写图谱间接触发 |
| 可替换 | 换钉钉为飞书只改 Connector | Function 不受影响 |

### Function 通过 Connector 访问外部系统

```python
@register_function("fetch_recent_logs")
def fetch_recent_logs(service_name: str, time_range: str = "1h", **kwargs):
    connector = get_connector("loki")
    if connector is None or not connector.health_check():
        return FunctionResult(success=False, data={"error": "Loki 不可用"})
    
    raw_logs = connector.fetch({
        "query": f'{{service="{service_name}"}}',
        "start": f"now-{time_range}",
        "end": "now",
        "limit": 500,
    })
    errors = [log for log in raw_logs if log.get("level") == "ERROR"]
    return FunctionResult(success=True, data={"errors": errors, "total": len(raw_logs)})
```

---

## 三、SAGA 补偿机制（修正 3）

### 核心设计

```python
# src/layerkg/saga.py
from dataclasses import dataclass, field
from typing import Any, Callable, Literal
from datetime import datetime, UTC
import asyncio

@dataclass
class SagaStep:
    name: str
    action: Callable[..., Any]      # 同步或异步函数均可
    compensate: Callable[..., Any]  # 同步或异步函数均可
    completed: bool = False
    result: Any = None

@dataclass
class SagaExecution:
    saga_id: str
    action_name: str
    steps: list[SagaStep] = field(default_factory=list)
    status: Literal["running", "completed", "compensating", "failed", "timeout", "compensation_failed"] = "running"
    error: str | None = None

class SagaOrchestrator:
    def __init__(self, audit_logger, graph_store=None, total_timeout: float = 300.0):
        self._audit = audit_logger
        self._graph_store = graph_store  # 用于持久化 SAGA 状态
        self._total_timeout = total_timeout
    
    def execute(self, saga: SagaExecution) -> SagaExecution:
        """执行 SAGA：全部成功则完成，任一失败则逆序补偿。
        
        注意：当前项目使用同步 Neo4j driver，SAGA 也保持同步。
        如果将来迁移到 AsyncGraphDatabase，再改为 async。
        """
        # 持久化 SAGA 起始状态到 Neo4j
        self._persist_saga(saga)
        
        import time
        start_time = time.time()
        
        for i, step in enumerate(saga.steps):
            if time.time() - start_time > self._total_timeout:
                saga.status = "timeout"
                saga.error = f"SAGA exceeded {self._total_timeout}s"
                self._compensate(saga)
                self._persist_saga(saga)
                return saga
            
            try:
                prev_result = saga.steps[i-1].result if i > 0 else None
                result = step.action(prev_result)
                # 如果 action 返回协程，用 asyncio.run 执行
                if asyncio.iscoroutine(result):
                    result = asyncio.get_event_loop().run_until_complete(result)
                step.result = result
                step.completed = True
                self._persist_saga(saga)  # 每步完成后持久化
            except Exception as e:
                saga.status = "compensating"
                saga.error = str(e)
                self._persist_saga(saga)
                self._compensate(saga)
                if saga.status != "compensation_failed":
                    saga.status = "failed"
                self._persist_saga(saga)
                return saga
        
        saga.status = "completed"
        self._persist_saga(saga)
        return saga
    
    def _compensate(self, saga: SagaExecution) -> None:
        compensation_failures = []
        for step in reversed(saga.steps):
            if step.completed and step.compensate:
                try:
                    result = step.compensate(step.result)
                    if asyncio.iscoroutine(result):
                        asyncio.get_event_loop().run_until_complete(result)
                except Exception as e:
                    compensation_failures.append({
                        "step": step.name,
                        "error": str(e),
                    })
        
        if compensation_failures:
            saga.status = "compensation_failed"
            # 放入人工干预队列
            self._persist_saga(saga, compensation_failures=compensation_failures)
    
    def _persist_saga(self, saga: SagaExecution, **extra) -> None:
        """持久化 SAGA 状态到 Neo4j（进程崩溃可恢复）。"""
        if self._graph_store is None:
            return
        self._graph_store.merge_node("SagaExecution", {
            "id": saga.saga_id,
            "action_name": saga.action_name,
            "status": saga.status,
            "error": saga.error or "",
            "completed_steps": [s.name for s in saga.steps if s.completed],
            "updated_at": datetime.now(UTC).isoformat(),
            **extra,
        })
    
    def recover_stuck_sagas(self) -> list[SagaExecution]:
        """查询需要人工干预的 SAGA（补偿失败/超时）。"""
        if self._graph_store is None:
            return []
        stuck = self._graph_store.query(
            'MATCH (s:SagaExecution) WHERE s.status IN ["compensation_failed", "timeout"] RETURN s'
        )
        return stuck
```

### 使用示例

```python
async def transfer_inventory(ctx: ActionContext):
    saga = SagaExecution(saga_id="inv-001", action_name="transfer")
    q = ctx.match_data["quantity"]
    from_id = ctx.match_data["from_id"]
    to_id = ctx.match_data["to_id"]
    
    # 定义显式的步骤函数和补偿函数（不用 lambda，可测试、可类型检查）
    async def deduct_source(_prev):
        return await ctx.call_function("update_entity",
            entity_name=from_id, updates={"stock": -q})
    
    async def compensate_deduct(_prev):
        return await ctx.call_function("update_entity",
            entity_name=from_id, updates={"stock": +q})
    
    async def add_target(_prev):
        return await ctx.call_function("update_entity",
            entity_name=to_id, updates={"stock": +q})
    
    async def compensate_add(_prev):
        return await ctx.call_function("update_entity",
            entity_name=to_id, updates={"stock": -q})
    
    async def create_log(_prev):
        return await ctx.call_function("create_entity",
            entity_type="TransferLog",
            properties={"from": from_id, "to": to_id, "quantity": q})
    
    async def compensate_delete_log(prev):
        if prev and prev.success:
            return await ctx.call_function("delete_entity", entity_id=prev.data["id"])
    
    saga.steps = [
        SagaStep(name="deduct_source", action=deduct_source, compensate=compensate_deduct),
        SagaStep(name="add_target", action=add_target, compensate=compensate_add),
        SagaStep(name="create_log", action=create_log, compensate=compensate_delete_log),
    ]
    
    result = SagaOrchestrator(ctx.audit, ctx.graph_store).execute(saga)
    return FunctionResult(success=result.status == "completed", data={"saga_id": result.saga_id})
```

---

## 四、统一事务管理（修正 4）

```python
# src/layerkg/transaction.py
from layerkg.schema import VALID_ENTITY_LABELS, RELATION_CONSTRAINTS

class TransactionManager:
    """Neo4j 原生事务管理器。
    
    注意：当前项目使用同步 Neo4j driver（neo4j python driver v5.x），
    所有事务操作都是同步的，不需要 async/await。
    如果将来迁移到 AsyncGraphDatabase，再去掉同步包装。
    """
    
    def __init__(self, driver):
        self._driver = driver
    
    def run_atomic(self, operations: list[dict]) -> list[dict]:
        """在一个事务内执行多步写操作，失败自动回滚。
        
        Args:
            operations: [{"cypher": str, "params": dict}, ...]
        
        Returns:
            每步操作的查询结果列表。
        """
        with self._driver.session() as session:
            tx = session.begin_transaction()
            results = []
            try:
                for op in operations:
                    result = tx.run(op["cypher"], op.get("params", {}))
                    records = [record.data() for record in result]
                    results.append(records)
                tx.commit()
                return results
            except Exception:
                tx.rollback()
                raise

class Neo4jTransaction:
    """事务包装器，供 Function 使用。
    
    用法:
        with tx_mgr.transaction() as tx:
            tx.create_entity("CodeEntity", {"name": "Cache", ...})
            tx.update_entity("CodeEntity", "cache-001", {"lines": 320})
            tx.create_relation("cache-001", "cs-001", "CHANGED_IN")
            # 任一失败，全部自动回滚
    """
    def __init__(self, tx):
        self._tx = tx
        # 白名单校验：只允许 Schema 中定义的实体标签
        from layerkg.schema import VALID_ENTITY_LABELS
        self._valid_labels = set(VALID_ENTITY_LABELS) | {
            "TempRequest", "TempDiagnosis", "TempApproval",
            "TransferLog", "SagaExecution",
        }
    
    def _validate_label(self, label: str) -> None:
        """防注入：只允许白名单标签。"""
        if label not in self._valid_labels:
            raise ValueError(f"Invalid entity label: {label}. Must be one of {self._valid_labels}")
    
    def _validate_rel_type(self, rel_type: str) -> None:
        """防注入：只允许 Schema 中定义的关系类型。"""
        from layerkg.schema import RELATION_CONSTRAINTS
        valid_rels = set(RELATION_CONSTRAINTS.keys())
        if rel_type not in valid_rels:
            raise ValueError(f"Invalid relation type: {rel_type}. Must be one of {valid_rels}")
    
    def run(self, cypher: str, params: dict | None = None):
        result = self._tx.run(cypher, params or {})
        return [record.data() for record in result]
    
    def create_entity(self, label: str, properties: dict):
        self._validate_label(label)
        return self.run(f"CREATE (n:`{label}` $props) RETURN n", {"props": properties})
    
    def update_entity(self, label: str, entity_id: str, updates: dict):
        self._validate_label(label)
        return self.run(
            f"MATCH (n:`{label}` {{id: $id}}) SET n += $updates RETURN n",
            {"id": entity_id, "updates": updates}
        )
    
    def create_relation(self, from_id: str, to_id: str, rel_type: str, properties: dict | None = None):
        self._validate_rel_type(rel_type)
        if properties:
            return self.run(
                f"MATCH (a {{id: $from}}), (b {{id: $to}}) "
                f"CREATE (a)-[r:`{rel_type}` $props]->(b) RETURN r",
                {"from": from_id, "to": to_id, "props": properties}
            )
        return self.run(
            f"MATCH (a {{id: $from}}), (b {{id: $to}}) "
            f"CREATE (a)-[r:`{rel_type}`]->(b) RETURN r",
            {"from": from_id, "to": to_id}
        )
```

**SAGA 和事务的关系：**
- 单实体多属性更新 → 用 TransactionManager（原生事务，一步回滚）
- 跨实体多步操作 → 用 SAGA（每步可能涉及不同实体/外部系统，需要逐步补偿）
- 两者可组合：SAGA 的某个 step 内部可以用 TransactionManager

---

## 五、FunctionRunner（修正 5）

```python
# src/layerkg/function_runner.py
import asyncio
from dataclasses import dataclass
from typing import Any, Callable

@dataclass
class ExecutionPolicy:
    timeout: float = 30.0
    max_retries: int = 2
    retry_delay: float = 1.0
    fallback: Any = None
    concurrency_limit: int = 5

@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    recovery_timeout: float = 60.0
    _failures: int = 0
    _last_failure_time: float = 0.0
    _state: str = "closed"
    
    @property
    def is_open(self) -> bool:
        if self._state == "open":
            import time
            if time.time() - self._last_failure_time > self.recovery_timeout:
                self._state = "half_open"
                return False
            return True
        return False
    
    def record_success(self):
        self._failures = 0
        self._state = "closed"
    
    def record_failure(self):
        import time
        self._failures += 1
        self._last_failure_time = time.time()
        if self._failures >= self.failure_threshold:
            self._state = "open"

class FunctionRunner:
    """统一 Function 执行器 — 超时、重试、熔断、并发控制。"""
    
    def __init__(self):
        self._breakers: dict[str, CircuitBreaker] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}
    
    async def run(self, func_name: str, func: Callable, args: dict, 
                  policy: ExecutionPolicy) -> Any:
        breaker = self._breakers.setdefault(func_name, CircuitBreaker())
        
        if breaker.is_open:
            if policy.fallback is not None:
                return policy.fallback
            raise RuntimeError(f"Circuit breaker open for {func_name}")
        
        sem = self._semaphores.setdefault(func_name, asyncio.Semaphore(policy.concurrency_limit))
        last_error = None
        
        async with sem:
            for attempt in range(policy.max_retries + 1):
                try:
                    result = func(**args)
                    if asyncio.iscoroutine(result):
                        result = await asyncio.wait_for(result, timeout=policy.timeout)
                    breaker.record_success()
                    return result
                except asyncio.TimeoutError:
                    last_error = f"Timeout {policy.timeout}s (attempt {attempt+1})"
                    breaker.record_failure()
                except Exception as e:
                    last_error = str(e)
                    breaker.record_failure()
                
                if attempt < policy.max_retries:
                    await asyncio.sleep(policy.retry_delay * (2 ** attempt))
            
            raise RuntimeError(f"{func_name} failed: {last_error}")
    
    async def run_parallel(self, tasks: list[tuple[str, Callable, dict, ExecutionPolicy]]) -> list[Any]:
        coros = [self.run(name, func, args, policy) for name, func, args, policy in tasks]
        return await asyncio.gather(*coros, return_exceptions=True)
```

---

## 六、增强规则引擎（修正 6）

```python
# src/layerkg/rule_engine.py
from collections import OrderedDict
import asyncio

class RuleEngine:
    def __init__(self, graph_store, event_bus, config: dict | None = None):
        self._graph_store = graph_store
        self._event_bus = event_bus
        self._rules: list[TriggerRule] = []
        self._orchestrators: dict[str, Callable] = {}
        
        # 幂等去重（OrderedDict 保证插入顺序，淘汰最早的条目）
        self._processed: OrderedDict[str, None] = OrderedDict()
        self._processed_max = 10000
        
        # 并发控制（背压）
        self._semaphore = asyncio.Semaphore(
            (config or {}).get("max_concurrent_actions", 3)
        )
        
        # depth 可配置
        self._default_max_depth = (config or {}).get("default_max_depth", 5)
        
        # 死信队列
        self._dead_letters: list[dict] = []
    
    def register(self, rule: TriggerRule, orchestrator: Callable) -> None:
        self._rules.append(rule)
        self._orchestrators[rule.action_name] = orchestrator
    
    async def _on_event(self, event: "ButlerEvent") -> None:
        current_depth = event.payload.get("_trigger_depth", 0)
        max_depth = self._get_max_depth(event)
        
        if current_depth >= max_depth:
            self._dead_letters.append({"reason": "max_depth", "depth": current_depth, "event": event.payload})
            return
        
        for rule in self._rules:
            # 幂等去重（加 rule.name 防同一 depth 不同 Action 重复触发）
            event_key = f"{event.event_type}:{event.payload.get('id', '')}:{current_depth}:{rule.name}"
            if event_key in self._processed:
                continue
            self._processed[event_key] = None
            # 有序淘汰：超过上限时删除最早的 50%
            if len(self._processed) > self._processed_max:
                for _ in range(self._processed_max // 2):
                    self._processed.popitem(last=False)
            
            try:
                match_data_list = rule.trigger(event, self._graph_store)
                if match_data_list is None:
                    continue
                for match_data in match_data_list:
                    async with self._semaphore:
                        await self._dispatch(rule, match_data, current_depth)
            except Exception as e:
                self._dead_letters.append({"reason": "rule_error", "rule": rule.name, "error": str(e)})
    
    def _get_max_depth(self, event) -> int:
        label = event.payload.get("label", "")
        for rule in self._rules:
            if rule.entity_type == label:
                return rule.max_depth
        return self._default_max_depth
    
    def get_dead_letters(self) -> list[dict]:
        return list(self._dead_letters)
```

---

## 七、IntentType 从 YAML 自动生成（修正 7）

```python
# src/layerkg/agent/intent_router.py
import yaml
from pathlib import Path

def build_intent_map(yaml_path: str | Path) -> dict[str, dict]:
    """从 ontology_actions.yaml 自动生成 intent_type 映射表。
    
    Returns: {intent_type: {"action": str, "description": str, "trigger_hint": str}}
    """
    with open(yaml_path) as f:
        config = yaml.safe_load(f)
    
    intent_map = {}
    for action_name, action_def in config.get("actions", {}).items():
        intent_type = action_def.get("intent_type", action_name)
        # 冲突检测：不允许两个 Action 映射到同一个 intent_type
        if intent_type in intent_map:
            raise ValueError(
                f"Duplicate intent_type '{intent_type}': "
                f"both '{intent_map[intent_type]['action']}' and '{action_name}' map to it"
            )
        intent_map[intent_type] = {
            "action": action_name,
            "description": action_def.get("description", ""),
            "trigger_hint": action_def.get("trigger_hint", ""),
        }
    return intent_map

def build_intent_prompt(intent_map: dict[str, dict]) -> str:
    """生成 Agent prompt 中的意图路由部分。"""
    lines = ["可用操作意图："]
    for intent_type, info in intent_map.items():
        lines.append(f"  - {intent_type}: {info['trigger_hint']}")
    lines.append("用户意图匹配上述描述时，调用 express_intent(intent_type, target, details)。")
    lines.append("不匹配时，用查询工具直接回答。")
    return "\n".join(lines)
```

**YAML 示例：**
```yaml
actions:
  refactor:
    description: "重构代码实体"
    intent_type: refactor
    trigger_hint: "用户要求重构、拆分、优化代码，或说代码太臃肿/太长/太复杂"
    submission_criteria: "target exists AND (lines > 100 OR branches > 15)"
    function: execute_refactor
    approval: false
    max_depth: 6

  diagnose:
    description: "诊断告警/故障"
    intent_type: diagnose
    trigger_hint: "用户报告故障、错误、异常、告警、服务不可用"
    submission_criteria: "target exists"
    function: execute_diagnose
    approval: false
    max_depth: 8

  rollback:
    description: "回滚变更"
    intent_type: rollback
    trigger_hint: "用户要求回滚、撤销、恢复"
    submission_criteria: "alert status == root_cause_confirmed"
    function: execute_rollback
    approval:
      required: true
      timeout: 1800
    max_depth: 4
```

**Agent prompt 自动生成：**
```
可用操作意图：
  - refactor: 用户要求重构、拆分、优化代码，或说代码太臃肿/太长/太复杂
  - diagnose: 用户报告故障、错误、异常、告警、服务不可用
  - rollback: 用户要求回滚、撤销、恢复
用户意图匹配上述描述时，调用 express_intent(intent_type, target, details)。
不匹配时，用查询工具直接回答。
```

---

## 八、Schema 数据模型和触发规则分离（修正 8）

```python
# schema.py — 只保留数据模型（稳定）
@dataclass
class CodeEntity:
    name: str
    file_path: str
    entity_type: str  # function/class/interface/module/file
    lines: int = 0
    branches: int = 0
    ...

# 不再在 schema.py 中定义触发规则
# 触发规则移到 rules/ 目录下，每个文件对应一类触发规则

# rules/refactor_rules.py
def validate_refactor_request(event, graph_store) -> list[dict] | None:
    """触发规则：重构请求验证。"""
    ...

# rules/alert_rules.py
def auto_diagnose_alert(event, graph_store) -> list[dict] | None:
    """触发规则：告警自动诊断。"""
    ...
```

**分离原则：**
- `schema.py` 定义"存在什么"（实体、属性、关系、约束） — 稳定不变
- `rules/` 定义"什么时候触发"（触发规则） — 随业务演进
- `ontology_actions.yaml` 声明"能做什么"（Action、Function） — 随场景扩展

---

## 九、neo4j_store 事件发射注入点（修正 9）

```python
# neo4j_store.py — 在所有写操作中注入事件发射

class Neo4jGraphStore(GraphStore):
    def __init__(self, ..., event_bus: "EventBus | None" = None):
        self._event_bus = event_bus
    
    def _emit(self, event_type: str, label: str, entity_id: str, 
              properties: dict, depth: int = 0) -> None:
        if self._event_bus:
            self._event_bus.publish_sync(ButlerEvent(
                event_type=event_type,
                payload={
                    "label": label, 
                    "id": entity_id,
                    "properties": properties,
                    "_trigger_depth": depth,
                }
            ))
    
    # 注入点清单（所有写操作都必须发射事件）：
    
    def merge_node(self, label, properties):
        result = ...  # 原有逻辑
        self._emit("node.merged", label, properties.get("id", ""), properties)
        return result
    
    def merge_relation(self, rel_type, from_id, to_id, properties=None):
        result = ...  # 原有逻辑
        self._emit("relation.merged", rel_type, from_id, properties or {})
        return result
    
    def update_node(self, label, entity_id, updates, depth=0):
        result = ...  # 原有逻辑
        self._emit("node.updated", label, entity_id, updates, depth)
        return result
    
    def delete_node(self, label, entity_id):
        result = ...  # 原有逻辑
        self._emit("node.deleted", label, entity_id, {})
        return result
    
    def delete_relation(self, rel_type, from_id, to_id):
        result = ...  # 原有逻辑
        self._emit("relation.deleted", rel_type, from_id, {})
        return result
```

**注入点完整清单：**

| 写操作 | 事件类型 | 必须发射 |
|--------|---------|---------|
| merge_node | node.merged | ✅ |
| merge_relation | relation.merged | ✅ |
| update_node | node.updated | ✅ |
| delete_node | node.deleted | ✅ |
| delete_relation | relation.deleted | ✅ |
| batch_merge_nodes | batch.merged | ✅ |
| execute_cypher（写操作） | cypher.executed | ⚠️ 可选 |

---

## 十、完整闭环链路（最终版）

```
用户："Cache太臃肿了，帮我重构"

1. 意图层：Agent 匹配 trigger_hint → intent_type=refactor
2. 意图层：Agent 调 express_intent("refactor", "Cache", {"reason": "too large"})
3. 语义层：neo4j_store.merge_node("TempRequest", {...}) → 发射 node.merged 事件
4. 控制层：RuleEngine 收到事件 → 匹配触发规则 validate_refactor_request → 匹配成功
5. 控制层：ActionShell 检查 Submission Criteria → Cache 存在 AND lines=320>100 ✅
6. 控制层：ActionShell 用 FunctionRunner 调用 Function
7. 能力层：Function check_refactor_eligibility(Cache) → 读语义层 → eligible=True
8. 能力层：Function execute_refactor(Cache, plan) → 写语义层（用 TransactionManager 保证原子性）
         → 创建 ChangeSetEntity
         → 建立 AFFECTS 关系（ChangeSet → Cache）
9. 语义层：neo4j_store 发射 node.merged + relation.merged 事件
10.控制层：RuleEngine 匹配新事件 → ChangeSetEntity 新增 → 触发 notify_stakeholders Action
11.能力层：Function find_affected_owners(ChangeSet) → 读语义层
12.连接层：DingTalkConnector.push(通知内容) → 发送钉钉消息
13.意图层：Agent 调 query_status("Cache") → 告知用户：重构完成，已通知 8 个下游负责人
```

---

## 十一、扩展性总结

| 想做什么 | 改哪层 | 怎么改 | 例子 |
|---------|--------|--------|------|
| 新增业务场景 | 控制层 | YAML 加 Action 声明 + rules/ 加触发函数 | 加"代码审查"Action |
| 新增外部数据源 | 连接层 | 实现 Connector 接口 | 接 Jira 工单 |
| 新增分析能力 | 能力层 | 写一个领域 Function + 装饰器注册 | 加"安全漏洞扫描" |
| 新增实体类型 | 语义层 | schema.py 加实体定义 | 加 VulnerabilityEntity |
| 新增触发规则 | 控制层 | rules/ 加 Python 条件函数 | "高危漏洞自动创建工单" |
| 换外部系统 | 连接层 | 换 Connector 实现 | 钉钉换飞书 |

**核心原则：每层只依赖下一层，不改骨架。**