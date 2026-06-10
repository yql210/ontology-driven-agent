# LayerKG V3.4 Phase B 实施计划 — TransactionManager + SAGA + FunctionRunner

## 目标
为 ActionExecutor 添加事务安全、多步编排、重试/熔断能力。Phase A 已完成基础闭环（express_intent → ActionExecutor → Function 链），Phase B 补齐基础设施。

## 前置条件
- Phase A 已完成（commit 3f81bbc, 1265 tests）
- action_types.py, action_executor.py, functions/registry.py 已存在
- V3.4 设计稿中 2.3/2.4/3.2 节定义了接口

## 不做的事
- 不实现 Connector（Phase D）
- 不实现领域 Function（Phase D）
- 不修改 graph.py / prompt.py / tools.py

---

## Task 1: 新建 ExecutionPolicy 数据类

文件: `src/layerkg/execution_policy.py` (新建)

```python
@dataclass
class ExecutionPolicy:
    max_retries: int = 2
    retry_delay: float = 1.0
    concurrency_limit: int = 5
    timeout: float = 60.0
    fallback: Callable | None = None
```

测试: `tests/unit/test_execution_policy.py`
- test_defaults
- test_custom_values
- test_fallback_none_by_default

---

## Task 2: 新建 CircuitBreaker

文件: `src/layerkg/circuit_breaker.py` (新建)

按 V3.4 设计稿 3.2 节实现：
- 三态：closed → open → half_open
- is_open 属性
- record_success / record_failure
- recovery_timeout 后半开

测试: `tests/unit/test_circuit_breaker.py`
- test_initial_state_closed
- test_opens_after_threshold_failures
- test_half_open_after_recovery_timeout
- test_success_resets_to_closed
- test_record_failure_increments

---

## Task 3: 新建 FunctionRunner

文件: `src/layerkg/function_runner.py` (新建)

```python
class FunctionRunner:
    def __init__(self, graph_store=None, connector_registry=None):
        self._graph_store = graph_store
        self._connectors = connector_registry
        self._policies: dict[str, ExecutionPolicy] = {}
        self._breakers: dict[str, CircuitBreaker] = {}
    
    def set_policy(self, func_name: str, policy: ExecutionPolicy):
        self._policies[func_name] = policy
    
    def run(self, func_name: str, ctx: ActionContext, **kwargs) -> FunctionResult:
        fn = get_function(func_name)
        if not fn:
            return FunctionResult(success=False, error=f"Unknown function: {func_name}")
        
        breaker = self._breakers.setdefault(func_name, CircuitBreaker())
        if breaker.is_open:
            # 尝试 fallback
            policy = self._policies.get(func_name)
            if policy and policy.fallback:
                return policy.fallback(ctx, **kwargs)
            return FunctionResult(success=False, error="Circuit breaker open")
        
        policy = self._policies.get(func_name, ExecutionPolicy())
        last_error = None
        
        for attempt in range(policy.max_retries + 1):
            try:
                result = fn(ctx, **kwargs)
                breaker.record_success()
                return result
            except Exception as e:
                last_error = e
                if attempt < policy.max_retries:
                    time.sleep(policy.retry_delay * (2 ** attempt))
                else:
                    breaker.record_failure()
        
        return FunctionResult(success=False, error=str(last_error))
    
    def run_batch(self, func_names: list[str], ctx: ActionContext, **kwargs) -> list[FunctionResult]:
        """顺序执行多个 Function，全部执行（不停在失败处）。"""
        results = []
        for name in func_names:
            results.append(self.run(name, ctx, **kwargs))
        return results
```

测试: `tests/unit/test_function_runner.py`
- test_run_success（注册一个 mock Function）
- test_run_unknown_function
- test_run_with_retry（第 1 次失败第 2 次成功）
- test_run_circuit_breaker_open
- test_run_fallback_when_breaker_open
- test_run_all_retries_exhausted
- test_run_batch

---

## Task 4: 新建 TransactionManager

文件: `src/layerkg/transaction_manager.py` (新建)

按 V3.4 设计稿 2.3 节实现：
- TransactionManager(driver).run_atomic(operations) — 多步写操作，失败自动回滚
- Neo4jTransaction(tx) — 白名单防注入的 create_entity / create_relation

```python
class TransactionManager:
    def __init__(self, driver):
        self._driver = driver
    
    def run_atomic(self, operations: list[dict]) -> list[list[dict]]:
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
    def __init__(self, tx):
        self._tx = tx
        from layerkg.schema import VALID_ENTITY_LABELS
        from layerkg.constraints import RELATION_CONSTRAINTS
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

测试: `tests/unit/test_transaction_manager.py`（用 mock driver）
- test_run_atomic_success
- test_run_atomic_rollback_on_failure
- test_neo4j_transaction_create_entity_invalid_label
- test_neo4j_transaction_create_relation_invalid_type
- test_neo4j_transaction_create_entity_success
- test_neo4j_transaction_create_relation_success

---

## Task 5: 新建 SAGA 编排器

文件: `src/layerkg/saga.py` (新建)

按 V3.4 设计稿 2.4 节（同步版）：
- SagaStep(name, action, compensation)
- SagaExecution(id, steps, status, graph_store 持久化)
- SagaOrchestrator.execute(saga_definition, ctx)

```python
@dataclass
class SagaStep:
    name: str
    action: Callable[[ActionContext], FunctionResult]
    compensation: Callable[[ActionContext], FunctionResult] | None = None

class SagaExecution:
    def __init__(self, saga_id: str, steps: list[SagaStep], graph_store=None):
        self.id = saga_id
        self.steps = steps
        self.completed: list[int] = []  # 已完成步骤的索引
        self.status: Literal["pending", "running", "completed", "compensating", "compensated", "failed"] = "pending"
        self._graph_store = graph_store
    
    def persist(self):
        """持久化 SAGA 状态到 Neo4j。"""
        if self._graph_store:
            self._graph_store.merge_node(
                "SagaExecution", self.id,
                {"status": self.status, "completed_steps": json.dumps(self.completed)}
            )

class SagaOrchestrator:
    def execute(self, steps: list[SagaStep], ctx: ActionContext) -> ActionResult:
        execution = SagaExecution(
            saga_id=str(uuid.uuid4()),
            steps=steps,
            graph_store=ctx.graph_store if hasattr(ctx, 'graph_store') else None,
        )
        execution.status = "running"
        execution.persist()
        
        results = []
        try:
            for i, step in enumerate(steps):
                result = step.action(ctx)
                results.append(result)
                if not result.success:
                    # 触发补偿
                    execution.status = "compensating"
                    execution.persist()
                    self._compensate(execution, ctx)
                    execution.status = "failed"
                    execution.persist()
                    return ActionResult(
                        success=False, action_name="saga",
                        results=results, error=f"Step '{step.name}' failed",
                    )
                execution.completed.append(i)
                execution.persist()
            
            execution.status = "completed"
            execution.persist()
            return ActionResult(
                success=True, action_name="saga", results=results,
                summary=f"SAGA completed {len(steps)} steps",
            )
        except Exception as e:
            execution.status = "compensating"
            execution.persist()
            self._compensate(execution, ctx)
            execution.status = "failed"
            execution.persist()
            return ActionResult(
                success=False, action_name="saga",
                results=results, error=str(e),
            )
    
    def _compensate(self, execution: SagaExecution, ctx: ActionContext):
        for idx in reversed(execution.completed):
            step = execution.steps[idx]
            if step.compensation:
                try:
                    step.compensation(ctx)
                except Exception:
                    pass  # 补偿失败不中断其他补偿
```

测试: `tests/unit/test_saga.py`
- test_saga_all_steps_success
- test_saga_step_failure_triggers_compensation
- test_saga_exception_triggers_compensation
- test_saga_no_compensation_on_success
- test_saga_compensation_failure_doesnt_stop_others
- test_saga_persist_state

---

## Task 6: 集成 — ActionExecutor 使用 FunctionRunner

文件: `src/layerkg/action_executor.py` (修改)

改动：
- ActionExecutor 接受可选的 FunctionRunner 注入
- execute() 中如果 FunctionRunner 存在，用它替代 ctx.call_function()
- 无 FunctionRunner 时保持 Phase A 的行为（向后兼容）

```python
class ActionExecutor:
    def __init__(self, graph_store, yaml_path=None, function_runner=None):
        self._graph_store = graph_store
        self._function_runner = function_runner  # 可选
        ...
    
    def execute(self, intent_type, params):
        ...
        # 5. 执行 Function 链
        for func_name in config.functions:
            if self._function_runner:
                result = self._function_runner.run(func_name, ctx)
            else:
                result = ctx.call_function(func_name)
            ...
```

测试: `tests/unit/test_action_executor.py` 追加
- test_execute_with_function_runner
- test_execute_without_function_runner_fallback（确认旧行为不变）

---

## Task 7: 集成测试 + 清理验证

文件: `tests/integration/test_e2e_phase_b.py`

测试场景：
- test_e2e_function_runner_retry_and_success
- test_e2e_saga_success_path
- test_e2e_saga_failure_compensation

验证：
- `uv run pytest tests/ -v` — 全量通过
- `uv run ruff check src/ tests/` — clean（新文件）
- `uv run ruff format --check src/ tests/` — clean

---

## 执行批次

| 批次 | Tasks | 内容 | 卡点 | max-turns |
|------|-------|------|------|-----------|
| Batch 1 | 1-3 | ExecutionPolicy + CircuitBreaker + FunctionRunner | test_execution_policy + test_circuit_breaker + test_function_runner | 50 |
| Batch 2 | 4-5 | TransactionManager + SAGA | test_transaction_manager + test_saga | 50 |
| Batch 3 | 6-7 | ActionExecutor 集成 + E2E | 全量测试 + ruff | 50 |
