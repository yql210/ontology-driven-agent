# Phase 5: Governance Migration — Shape/ApprovalGate Adapt to Capability DAG, Guard Retires

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.
> **Code must be written by Claude Code CLI (`claude -p`), not delegate_task subagents.**
> Hermes does: planning, Claude Code review coordination, verification, commits.

**Goal:** Unify the dual-path constraint system (Shape + Guard Pipeline) into a single Shape-based path, extend governance to DAG-level execution, retire the legacy Guard Pipeline.

**Architecture:** Remove `ActionGuardPipeline` and its 5 guards from the runtime. `ShapeEvaluator` becomes the sole constraint mechanism, integrated into `DAGOrchestrator` for per-node pre-execution checks. `ApprovalGate` extended for DAG-level preflight scanning and node-level approval tokens.

**Tech Stack:** Python 3.13+, Neo4j, pytest, uv

**Baseline:** 1658 tests passing, 4 commits in Phase 4

**Review:** Claude Code review (delegate_task) identified 3 critical issues — all addressed in v2.

---

## Current vs Target Architecture

### Pre-Phase 5

```
express_intent()
  ├── Guard Pipeline check (ActionGuardPipeline)  ← 要退役
  ├── ApprovalGate.check() → GuardResultPolicy  ← 依赖 Guard Pipeline
  ├── ActionExecutor.execute()
  │     ├── if shape_registry: ShapeEvaluator    ← 唯一保留
  │     └── else: Guard Pipeline fallback        ← 要删除
  └── FunctionRunner.run() → FunctionDangerPolicy
```

### Post-Phase 5

```
express_intent()
  ├── ActionExecutor.execute()
  │     └── ShapeEvaluator (唯一约束路径，覆盖所有 5 个 Guard 的语义)
  ├── ApprovalGate.check() → ShapeBasedGuardPolicy
  └── FunctionRunner.run() → FunctionDangerPolicy

Planner.plan()
  └── DAGOrchestrator.preflight(shape_registry)
       └── DAGOrchestrator.execute()
            └── per-node: ShapeEvaluator → ALLOW/WARN/BLOCK/ESCALATE
```

### Guard → Shape 功能映射

| Guard | 功能 | Shape 替代方案 |
|-------|------|---------------|
| WhitelistGuard | 显式白名单短路放行 | Shape 的 `enabled: false` + `unless_field` 可覆盖豁免；显式白名单迁入 `constraint_overrides.yaml` → `ShapeRegistry.allow_set` |
| EntityExistsGuard | 实体存在性检查 | `ActionExecutor._resolve_entity()` 已在 L63-69 做了存在性检查（resolve 失败直接返回 error）。Guard 冗余，直接移除。 |
| EntityPropertyGuard | per-Action submission_criteria | 通用 criteria（如 `entity.lines > 100`）已有对应 Shape（shape:refactor_large_code_unit）。Task 5.2.5 审计其余 criteria，按需创建 Shape 或保留 per-action 检查。 |
| OntologyTraversalGuard | 图遍历 + 属性约束 | Shape path 机制完全覆盖。`value_mapping` 的差异化处置（restricted→BLOCK, confidential→WARN）通过拆分两个 Shape 实现。 |
| OntologyPropagationGuard | BFS 多层传播 + 聚合 | **Shape 的 PathCompiler 已支持 quantifier**（`CALLS{1,5}`, `CALLS+`），可表达多层遍历。`upstream_risk` 等价于 `path: "^CALLS{1,5} -> CodeEntity"` + `constraint: {field: entryCategory, operator: in, value: [http_api, rpc_service]}`。Task 5.0 新增 propagation-equivalent Shapes 到 shapes.yaml。 |

---

## Task Summary (v2 — 经审查修正)

| # | Task | Files | 合并说明 |
|:--|------|-------|------|
| 5.0 | shapes.yaml 扩展 — propagation + criteria Shapes | `shapes.yaml` | **新增**：覆盖 PropagationGuard 和残留 criteria |
| 5.1+2 | Guard 退役 — ActionExecutor + tools.py 同步移除 | `action_executor.py`, `tools.py`, `policies.py` | **合并**：避免中间 TypeError |
| 5.3 | 测试同步更新 — guard 测试删除/迁移 | 10 test files | **提前**：5.1+2 后立即执行 |
| 5.4 | constraints/__init__.py 清理导出 | `constraints/__init__.py` | |
| 5.5 | DAGOrchestrator Shape 集成 | `dag_orchestrator.py` | |
| 5.6 | DAGOrchestrator.preflight() | `dag_orchestrator.py` | |
| 5.7 | ApprovalGate DAG 适配 | `approval_gate.py` | |
| 5.8 | Planner→Orchestrator 桥接 | `execution/planner/bridge.py` | |
| 5.9 | 全量回归 + Guard 代码物理删除 + push | all | |

---

## Task 5.0: shapes.yaml 扩展 — Propagation + Criteria Shapes

**Objective:** 在 shapes.yaml 中补充等价于 PropagationGuard 和残留 submission_criteria 的 Shape 定义，确保迁移后功能不丢失。

**Files:**
- Modify: `src/ontoagent/pipeline/shapes.yaml`
- Modify: `src/ontoagent/execution/shape_registry.py`（如需要 allow_set 支持）
- Audit: `src/ontoagent/pipeline/ontology_actions.yaml`（submission_criteria）

**Step 1: 审计 submission_criteria**

```bash
grep -A2 "submission_criteria" src/ontoagent/pipeline/ontology_actions.yaml
```

找出所有 per-Action criteria。对于通用型（如 lines > 100）→ 已有 Shape 覆盖；对于特定型 → 新建 Shape 或保留。

**Step 2: 新增 propagation-equivalent Shape（upstream_risk）**

```yaml
- id: shape:upstream_call_chain_risk
  name: 上游调用链入口点检查
  description: "反向遍历 CALLS 调用链，检查上游是否有 HTTP/RPC 入口点"
  kind: operational
  target:
    resource_type: CodeEntity
    operation: UPDATE
  path: "^CALLS{1,5} -> CodeEntity"
  constraint:
    field: entryCategory
    operator: in
    value: [http_api, rpc_service]
  severity: warn
  priority: 6
  tags: [call_chain, entry_point, upstream]
  suggestion: |
    调用链上游存在 HTTP/RPC 入口点。建议:
    (a) 确认修改不影响 API 契约
    (b) 检查下游调用方兼容性
    (c) 必要时通知 API 消费者
```

`^CALLS{1,5}` 语法说明：`^` 表示反向遍历，`{1,5}` 是 PathCompiler 已支持的 quantifier（生成 `[:CALLS*1..5]`）。

**Step 3: 新增 criteria-equivalent Shapes（审计后按需添加）**

对每条残留 submission_criteria，判断是否可转为 Shape：
- 通用 → 新增 Shape（如 `entity.branches > 5` → `shape:complex_branch`）
- 特定 → 保留在 YAML，但将检查逻辑从 EntityPropertyGuard 迁到 ActionExecutor 内联

**Step 4: 添加 ShapeRegistry allow_set 支持（WhitelistGuard 替代）**

```python
# shape_registry.py 新增
class ShapeRegistry:
    def __init__(self, valid_labels: set[str], allow_set: set[str] | None = None):
        ...
        self._allow_set = allow_set or set()

    def is_allowed(self, entity_label: str, entity_name: str) -> bool:
        """Check if entity is whitelisted (short-circuit ALLOW)."""
        return f"{entity_label}:{entity_name}" in self._allow_set
```

ShapeEvaluator 在 evaluate() 前先检查 allow_set。

**Verification:**
```bash
uv run pytest tests/unit/test_shape_evaluator.py -v
# New tests: test_upstream_risk_shape, test_allow_set_short_circuit
```

**Commit:**
```bash
git add src/ontoagent/pipeline/shapes.yaml src/ontoagent/execution/shape_registry.py
git commit -m "feat: Phase 5.0 — propagation Shapes + allow_set in ShapeRegistry"
```

---

## Task 5.1+2: Guard 退役 — ActionExecutor + tools.py + policies.py 同步移除

**Objective:** 同步删除 ActionExecutor 的 guard_pipeline 参数、tools.py 的 Guard Pipeline 构建、policies.py 的 GuardResultPolicy，一次 commit 完成避免中间 TypeError。

**Files:**
- Modify: `src/ontoagent/execution/action_executor.py`
- Modify: `src/ontoagent/agent/tools.py`
- Modify: `src/ontoagent/execution/constraints/policies.py`

### action_executor.py

**移除 guard_pipeline 导入和参数：**

```python
# 删除 L11-12:
# from ontoagent.execution.constraints.guard_pipeline import ActionGuardPipeline
# from ontoagent.execution.constraints.guards import EntityExistsGuard, EntityPropertyGuard

# __init__ 删除 guard_pipeline 参数和 self._guard_pipeline
class ActionExecutor:
    def __init__(
        self,
        graph_store: Any,
        yaml_path: Path | None = None,
        function_runner: Any | None = None,
        shape_registry: Any | None = None,
    ) -> None:
        self._graph_store = graph_store
        self._function_runner = function_runner
        self._shape_registry = shape_registry
```

**简化 execute() 约束检查（L71-89）：**

```python
# Before: if-else branch on self._shape_registry
# After: 单一路径，shape_registry 为 None 时跳过约束检查（降级路径：无约束视为通过）
if not bypass_guard and self._shape_registry is not None:
    block_reason, warnings = self._check_with_shapes(entity, config)
    if block_reason:
        return ActionResult(
            success=False,
            action_name=config.name,
            error=block_reason,
            warnings=warnings,
        )
```

降级路径说明：当 `shape_registry` 为 None（feature flag 关闭/shapes.yaml 缺失）时，**所有操作无约束通过**。这是显式设计选择——运维者关闭 feature flag 意味着主动放弃约束。

### tools.py

**_get_action_executor() 移除 Guard Pipeline 构建块（L957-1013）：**

```python
# 删除整个 Guard Pipeline 构建块（导入 + ConstraintEngine + ConstraintPropagator + ActionGuardPipeline + WhitelistGuard 等）
# 简化为：
shape_registry = _get_shape_registry()

_action_executor = ActionExecutor(
    graph_store,
    function_runner=_get_function_runner(),
    shape_registry=shape_registry,
)
```

**express_intent() 重写 guard check（L458-492）：**

```python
# Before: pipeline.check() → guard_checks list
# After: _check_with_shapes() → 直接在 kwargs 传 executor
if not skip_approval and approval_gate and executor._shape_registry is not None:
    decision = approval_gate.check(
        approval_ctx,
        config=config,
        graph_store=neo4j,
        executor=executor,
    )
```

**_get_approval_gate() 替换 GuardResultPolicy → ShapeBasedGuardPolicy：**

```python
from ontoagent.execution.constraints.policies import (
    ShapeBasedGuardPolicy, ActionApprovalPolicy, FunctionDangerPolicy
)
policies = [ShapeBasedGuardPolicy(), ActionApprovalPolicy(), FunctionDangerPolicy(function_meta)]
# 删除 policy.set_pipeline() 调用
```

### policies.py

**新增 ShapeBasedGuardPolicy + 标记 GuardResultPolicy deprecated：**

```python
class ShapeBasedGuardPolicy(ApprovalPolicy):
    """用 ShapeEvaluator 替代 ActionGuardPipeline 的审批策略。

    通过 kwargs['executor'] 获取 ActionExecutor，调用其 _check_with_shapes()
    方法评估 Shape 约束，根据 on_block/on_warn 配置决定审批级别。
    """

    def __init__(self, on_block: str = "require_approval", on_warn: str = "require_approval") -> None:
        self._on_block = on_block
        self._on_warn = on_warn

    @property
    def name(self) -> str:
        return "ShapeBasedGuardPolicy"

    def evaluate(self, context: ApprovalContext, **kwargs: Any) -> PolicyResult:
        executor = kwargs.get("executor")
        config = kwargs.get("config")
        if executor is None or config is None:
            return PolicyResult(policy_name=self.name, level=DecisionLevel.APPROVED,
                              reason="no executor/config")

        block_reason, warnings = executor._check_with_shapes(context.entity, config)

        if block_reason:
            if self._on_block == "auto_reject":
                return PolicyResult(policy_name=self.name, level=DecisionLevel.DENIED,
                                  reason=block_reason)
            return PolicyResult(policy_name=self.name, level=DecisionLevel.PENDING,
                              reason=block_reason,
                              details={"shape_block": block_reason, "warnings": warnings})

        if warnings and self._on_warn == "require_approval":
            return PolicyResult(policy_name=self.name, level=DecisionLevel.PENDING,
                              reason="WARN 级别约束需要确认",
                              details={"warnings": warnings})

        return PolicyResult(policy_name=self.name, level=DecisionLevel.APPROVED,
                          reason="shape check passed")


class GuardResultPolicy(ApprovalPolicy):
    """DEPRECATED: 用 ShapeBasedGuardPolicy 替代。Phase 5.9 物理删除。"""
    ...
```

**Verification:**
```bash
# 5.1+2 完成后，所有 guard 测试会挂（预期）。Task 5.3 立即修复。
# 先验证 import 无错误：
uv run python -c "from ontoagent.agent.tools import _get_action_executor; print('import ok')"
```

**Commit:**
```bash
git add src/ontoagent/execution/action_executor.py src/ontoagent/agent/tools.py \
        src/ontoagent/execution/constraints/policies.py
git commit -m "refactor: Phase 5.1+2 — remove guard pipeline, add ShapeBasedGuardPolicy"
```

---

## Task 5.3: 测试同步更新

**Objective:** 修复因 Guard Pipeline 移除导致的测试失败。guard 测试删除，其他测试迁移到 Shape-based mock。

**Files:**

| File | Tests | Action |
|------|:--:|--------|
| `tests/unit/test_guard_pipeline.py` | 26 | 删除文件 |
| `tests/unit/test_approval_gate.py` | 24 | GuardResultPolicy mock → ShapeBasedGuardPolicy mock |
| `tests/unit/execution/test_action_executor_shapes.py` | 10 | 移除 guard fallback 期望 |
| `tests/unit/execution/test_tools_express_intent.py` | 10 | Mock ShapeEvaluator 替代 guard pipeline |
| `tests/unit/agent/test_tools.py` | 6 | 更新 mock setup |
| `tests/unit/agent/test_ontology_tools.py` | 6 | 更新 mock setup |
| `tests/unit/test_shape_evaluator.py` | ~15 | 新增 allow_set + upstream_risk 测试 |
| `tests/integration/test_constraint_integration.py` | 4 | 使用 Shape 路径 |
| `tests/integration/test_e2e_action_v34.py` | 6 | 移除 guard 引用 |

**关键变更：**

1. 删除 `tests/unit/test_guard_pipeline.py`
2. `test_approval_gate.py`：替换 `GuardResultPolicy` → `ShapeBasedGuardPolicy`，mock `executor._check_with_shapes`
3. `test_tools_express_intent.py`：不再 mock `executor._guard_pipeline`，改为 mock `executor._shape_registry` 和 `executor._check_with_shapes`
4. `test_constraint_integration.py`：检查是否覆盖了 PropagationGuard 的 BFS 场景 — 如有，保留但改为走 Shape
5. `test_shape_evaluator.py`：新增 `test_allow_set_short_circuit`、`test_upstream_risk_multihop`

**Verification:**
```bash
uv run pytest tests/ -m "not integration" -q
# Expected: all unit tests green，比 baseline 少约 26 个（guard pipeline 删除）
```

**Commit:**
```bash
git add -A
git commit -m "test: Phase 5.3 — migrate tests from guard pipeline to Shape"
```

---

## Task 5.4: constraints/__init__.py 清理导出

**Objective:** 移除 guard 相关导出，保留仍在使用的内容。

**Files:**
- Modify: `src/ontoagent/execution/constraints/__init__.py`

**移除导出:**
- `ActionGuard`, `ActionGuardPipeline`
- `EntityExistsGuard`, `EntityPropertyGuard`, `OntologyPropagationGuard`, `OntologyTraversalGuard`, `WhitelistGuard`
- `GuardResultPolicy`

**保留导出:**
- `ApprovalGate`, `ApprovalPolicy`, `ShapeBasedGuardPolicy`
- `ConstraintEngine`, `ConstraintPropagator`（仍被 loader 使用）
- `FunctionDangerPolicy`, `ActionApprovalPolicy`
- `OntologyConstraintLoader`, `PropagationResult`, `PropagationRule`
- `aggregate_levels`

**Verification:**
```bash
uv run pytest tests/ -m "not integration" -q
# Expected: no import errors
```

**Commit:**
```bash
git add src/ontoagent/execution/constraints/__init__.py
git commit -m "refactor: Phase 5.4 — clean constraint exports"
```

---

## Task 5.5: DAGOrchestrator Shape 集成

**Objective:** 添加可选的 `ShapeEvaluator` 到 `DAGOrchestrator`，每个节点执行前评估 Shape。

**Files:**
- Modify: `src/ontoagent/execution/dag_orchestrator.py`

**Step 1: 扩展 NodeResult 和 ExecutionResult**

```python
@dataclass
class NodeResult:
    node_id: str
    status: str = "ok"  # 'ok', 'failed', 'skipped', 'blocked', 'needs_approval'
    output: dict = field(default_factory=dict)
    error: str | None = None
    shape_results: list[dict] = field(default_factory=list)  # NEW

@dataclass
class ExecutionResult:
    status: str = "completed"  # 'completed', 'failed', 'needs_approval'
    node_results: list[NodeResult] = field(default_factory=list)
    failed_node_id: str | None = None
    elapsed_ms: int = 0
    approval_nodes: list[str] = field(default_factory=list)  # NEW
```

**Step 2: DAGOrchestrator 接受 shape_evaluator**

```python
class DAGOrchestrator:
    def __init__(
        self,
        relations: list[tuple[str, str, str]] | None = None,
        shape_evaluator: Any | None = None,
    ) -> None:
        self._relations = relations or []
        self._shape_evaluator = shape_evaluator
```

**Step 3: 节点执行前 Shape 检查**

在 execute() 的节点循环中，调用 cap_fn 之前：

```python
# Shape check
entity = nd.get("entity")
if self._shape_evaluator is not None and entity is not None:
    operations = nd.get("operations", [])
    from ontoagent.domain.shapes import Severity
    shape_results_list = self._shape_evaluator.evaluate(entity, operations)
    
    triggered = [r for r in shape_results_list if r.triggered]
    shape_dicts = [
        {"shape_id": r.shape.id, "severity": r.severity.value, "suggestion": r.shape.suggestion}
        for r in triggered
    ]
    
    for r in triggered:
        if r.severity == Severity.BLOCK:
            node_results.append(NodeResult(
                node_id=node_id, status="blocked",
                error=f"Shape {r.shape.id}: {r.shape.suggestion}",
                shape_results=shape_dicts,
            ))
            failed = True; failed_node_id = node_id
            break
        elif r.severity == Severity.ESCALATE:
            # Mark for approval, don't block
            approval_nodes.append(node_id)
    else:
        # No BLOCK — proceed to execute
        ...
```

**Step 4: 节点 dict 格式扩展**

```python
nodes = [{
    "id": "A",
    "capability": lambda payload: {"result": "ok"},
    "produces": ["OrderData"],
    "entity": {"id": "123", "labels": ["CodeEntity"], "name": "func_x"},  # NEW
    "operations": [Operation.UPDATE],  # NEW
}]
```

**Verification:**
```bash
uv run pytest tests/unit/test_dag_orchestrator.py tests/unit/test_dag_compensation.py -v
```

**Commit:**
```bash
git add src/ontoagent/execution/dag_orchestrator.py tests/unit/test_dag_orchestrator.py
git commit -m "feat: Phase 5.5 — DAGOrchestrator per-node Shape evaluation"
```

---

## Task 5.6: DAGOrchestrator.preflight()

**Objective:** 新增 preflight() — 执行前扫描整棵 DAG，提前发现需要审批/被阻断的节点。

**Files:**
- Modify: `src/ontoagent/execution/dag_orchestrator.py`

```python
@dataclass
class PreflightResult:
    all_clear: bool
    blocked_nodes: dict[str, list[dict]]
    warn_nodes: dict[str, list[dict]]
    escalate_nodes: dict[str, list[dict]]

def preflight(self, nodes: list[dict]) -> PreflightResult:
    """Scan all nodes for shape constraints before execution."""
    blocked: dict[str, list[dict]] = {}
    warns: dict[str, list[dict]] = {}
    escalates: dict[str, list[dict]] = {}
    
    if self._shape_evaluator is None:
        return PreflightResult(all_clear=True, blocked_nodes={}, warn_nodes={}, escalate_nodes={})
    
    from ontoagent.domain.shapes import Severity
    
    for nd in nodes:
        entity = nd.get("entity")
        if entity is None:
            continue
        operations = nd.get("operations", [])
        results = self._shape_evaluator.evaluate(entity, operations)
        
        for r in results:
            if not r.triggered:
                continue
            info = {"shape_id": r.shape.id, "severity": r.severity.value, "suggestion": r.shape.suggestion}
            if r.severity == Severity.BLOCK:
                blocked.setdefault(nd["id"], []).append(info)
            elif r.severity == Severity.ESCALATE:
                escalates.setdefault(nd["id"], []).append(info)
            elif r.severity == Severity.WARN:
                warns.setdefault(nd["id"], []).append(info)
    
    return PreflightResult(
        all_clear=not blocked and not escalates,
        blocked_nodes=blocked, warn_nodes=warns, escalate_nodes=escalates,
    )
```

**Verification:**
```bash
uv run pytest tests/unit/test_dag_orchestrator.py -v
# New tests: test_preflight_all_clear, test_preflight_block, test_preflight_escalate
```

**Commit:**
```bash
git add src/ontoagent/execution/dag_orchestrator.py tests/unit/test_dag_orchestrator.py
git commit -m "feat: Phase 5.6 — DAGOrchestrator.preflight()"
```

---

## Task 5.7: ApprovalGate DAG 适配

**Objective:** 新增 check_dag() 和 resolve_node()，支持 DAG 级批量审批。

**Files:**
- Modify: `src/ontoagent/execution/constraints/approval_gate.py`

```python
def check_dag(self, preflight: Any, context: ApprovalContext) -> dict[str, str | None]:
    """Generate per-node approval tokens from PreflightResult.
    
    Returns dict[node_id → token | None].
    None = auto-approved or blocked (cannot proceed).
    """
    tokens: dict[str, str | None] = {}
    
    for node_id in preflight.escalate_nodes:
        token = generate_token(f"dag:{node_id}", context.target, context.session_id)
        self._pending[token] = PendingApproval(
            token=token,
            context=ApprovalContext(
                intent_type=f"dag_node:{node_id}",
                target=context.target, params=context.params,
                entity=context.entity, guard_checks=[], session_id=context.session_id,
            ),
            ttl=self._ttl,
        )
        tokens[node_id] = token
    
    for node_id in preflight.blocked_nodes:
        tokens[node_id] = None
    
    return tokens

def resolve_node(self, token: str, approved: bool) -> bool:
    """Resolve a single DAG node approval. Returns True if the node can proceed."""
    ctx = self.resolve(token, approved)
    return ctx is not None
```

**Verification:**
```bash
uv run pytest tests/unit/test_approval_gate.py -v
# New tests: test_check_dag_generates_tokens, test_check_dag_blocks, test_resolve_node
```

**Commit:**
```bash
git add src/ontoagent/execution/constraints/approval_gate.py tests/unit/test_approval_gate.py
git commit -m "feat: Phase 5.7 — ApprovalGate DAG-level check_dag()"
```

---

## Task 5.8: Planner→Orchestrator 桥接

**Objective:** PlanDAG → DAGOrchestrator node dicts 转换，含实体解析。

**Files:**
- Create: `src/ontoagent/execution/planner/bridge.py`
- Create: `tests/unit/execution/test_planner_bridge.py`

```python
"""Bridge: PlanDAG → DAGOrchestrator node dicts."""

from __future__ import annotations
from typing import Any
from ontoagent.execution.planner.data_types import PlanNode, PlanDAG


def plan_to_orchestrator_nodes(
    dag: PlanDAG,
    graph_store: Any,
) -> tuple[list[dict], list[tuple[str, str]]]:
    """Convert PlanDAG to DAGOrchestrator-compatible (nodes, edges).
    
    Resolves entities from graph_store for each PlanNode.
    """
    nodes: list[dict] = []
    for plan_node in dag.nodes:
        entity = _resolve_entity(graph_store, plan_node.sub_goal.description)
        nodes.append({
            "id": plan_node.id,
            "capability": _make_stub_capability(plan_node),
            "sub_goal": plan_node.sub_goal.description,
            "domain": plan_node.sub_goal.domain,
            "entity": entity,
            "operations": _infer_operations(plan_node),
        })
    return nodes, list(dag.edges)
```

**Verification:**
```bash
uv run pytest tests/unit/execution/test_planner_bridge.py -v
```

**Commit:**
```bash
git add src/ontoagent/execution/planner/bridge.py tests/unit/execution/test_planner_bridge.py
git commit -m "feat: Phase 5.8 — Planner→Orchestrator bridge"
```

---

## Task 5.9: 全量回归 + Guard 代码物理删除 + push

**Objective:** 全量测试、物理删除 guard 源文件、提交推送。

**Files:**
- Delete: `src/ontoagent/execution/constraints/guards.py`
- Delete: `src/ontoagent/execution/constraints/guard_pipeline.py`
- Modify: `src/ontoagent/execution/constraints/policies.py`（删除 GuardResultPolicy 类）

**Step 1: 全量回归**

```bash
uv run pytest tests/ -x --tb=short -q
# Must be all green
```

**Step 2: 物理删除 guard 源文件**

```bash
rm src/ontoagent/execution/constraints/guards.py
rm src/ontoagent/execution/constraints/guard_pipeline.py
```

**Step 3: 从 policies.py 删除 GuardResultPolicy 类**

**Step 4: 静态检查**

```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
uv run pyright src/
```

**Step 5: 最终回归 + push**

```bash
uv run pytest tests/ -x --tb=short -q
git add -A
git commit -m "chore: Phase 5.9 — physical guard code removal + full regression"
git push
```

**Claim-by-claim 验证:**
- [ ] `ActionExecutor.__init__` 无 `guard_pipeline` 参数
- [ ] `express_intent()` 走 Shape 路径
- [ ] `guard_pipeline.py` 物理删除
- [ ] `guards.py` 物理删除
- [ ] `GuardResultPolicy` 从 policies.py 删除
- [ ] shapes.yaml 含 upstream_risk Shape（`^CALLS{1,5}`）
- [ ] ShapeRegistry 含 allow_set
- [ ] DAGOrchestrator 含 shape_evaluator 参数 + preflight()
- [ ] ApprovalGate 含 check_dag() + resolve_node()
- [ ] 全量测试通过 + lint/type 通过

---

## Risk Table (v2 — 经审查补充)

| Risk | Likelihood | Mitigation |
|------|:--:|------------|
| **FIXED** PropagationGuard BFS 传播丢失 | — | Task 5.0 新增 `shape:upstream_call_chain_risk`，PathCompiler 已支持 quantifier |
| **FIXED** Submission Criteria 丢失 | — | Task 5.0 审计 ontology_actions.yaml，逐条迁移为 Shape |
| **FIXED** 中间 TypeError (5.1→5.2 分离) | — | 合并为 5.1+2，一次 commit |
| **FIXED** 测试长时间全红 | — | Task 5.3 紧跟 5.1+2，每个逻辑块自带测试更新 |
| Whitelist allow_set 格式不兼容 | Low | ShapeRegistry 新增 `is_allowed()` 方法，语义等价 |
| check_operation 返回格式 Breaking Change | Medium | Shape 返回格式不同（shape_id vs guard name），但 check_operation 是内部 Agent 工具，可控 |
| shape_registry 为 None 时约束真空 | Low | 显式设计：运维者关闭 feature flag = 主动放弃约束。记录在降级路径文档 |
| 集成测试 BFS 场景丢失 | Low | Task 5.3 审计 integration tests，BFS 场景改为 Shape 等效测试 |
| DAG 审批交互流程未定义 | Low | check_dag() → token 机制与现有 express_intent 的 approval_id 模式一致，不引入新交互模式 |

---

## Commit Plan (v2)

| # | Message | Tasks |
|:--|---------|:-----:|
| 1 | `feat: Phase 5.0 — propagation Shapes + allow_set in ShapeRegistry` | 5.0 |
| 2 | `refactor: Phase 5.1+2 — remove guard pipeline, add ShapeBasedGuardPolicy` | 5.1+2 |
| 3 | `test: Phase 5.3 — migrate tests from guard pipeline to Shape` | 5.3 |
| 4 | `refactor: Phase 5.4 — clean constraint exports` | 5.4 |
| 5 | `feat: Phase 5.5 — DAGOrchestrator per-node Shape evaluation` | 5.5 |
| 6 | `feat: Phase 5.6 — DAGOrchestrator.preflight()` | 5.6 |
| 7 | `feat: Phase 5.7 — ApprovalGate DAG-level check_dag()` | 5.7 |
| 8 | `feat: Phase 5.8 — Planner→Orchestrator bridge` | 5.8 |
| 9 | `chore: Phase 5.9 — physical guard code removal + full regression` | 5.9 |
