# Phase 5: 本体注解驱动约束框架（修订版 v2）

> 经 Claude Code 架构审查 + 产品/架构双重审核后修订

## 目标

将本体定义提升为 agent 行为约束的驱动源。框架提供通用能力，不绑定特定业务场景。demo-service 只是验证框架的示例本体。

## 架构总览

```
┌─ domain/ ─────────────────────────────────────┐
│  TraversalConstraint  PropertyConstraint       │
│  CompoundConstraint   RelationConstraint       │
│  validate_relation_constraint()  ← 加载时校验  │
└───────────────────────┬───────────────────────┘
                        │
┌─ execution/constraints/ ──────────────────────┐
│  ConstraintEngine        ← 执行遍历约束        │
│  ConstraintPropagator    ← BFS 传播约束属性    │
│  ActionGuardPipeline     ← 可插拔 guard 链     │
│  guards.py               ← 内置 guard 实现     │
└───────────────────────┬───────────────────────┘
                        │
┌─ execution/ ──────────────────────────────────┐
│  ActionExecutor  ← 注入 GuardPipeline          │
│  (保留 _check_criteria 向后兼容)               │
└───────────────────────┬───────────────────────┘
                        │
                      Agent
```

## 核心组件

### 1. TraversalConstraint（domain/）

```python
@dataclass
class TraversalConstraint:
    """通用遍历约束 — 不绑定任何业务域。加载时必须通过 validate_relation_constraint() 校验。"""
    source_label: str          # "CodeEntity"
    relation_chain: list[str]  # ["PROCESSES_DATA", "GOVERNED_BY"]
    target_label: str          # "ComplianceItem"
    collect_property: str      # "severity"
    value_mapping: dict[str, str]  # {"critical": "block", "high": "warn"}
    aggregation: str = "max"   # max | min | exists
```

### 2. PropertyConstraint + CompoundConstraint（domain/）

```python
@dataclass
class PropertyConstraint:
    """替代现有的 "entity.lines > 100" 字符串条件"""
    field: str
    operator: str  # ">" | ">=" | "==" | "!="
    value: Any

@dataclass
class CompoundConstraint:
    """AND/OR 组合约束"""
    operator: Literal["AND", "OR"]
    children: list[TraversalConstraint | PropertyConstraint | CompoundConstraint]
```

### 3. ConstraintEngine（execution/constraints/）

```python
class ConstraintEngine:
    def __init__(self, graph_store, constraints: list[TraversalConstraint]):
        # 加载时校验每个 constraint 的 relation_chain 合法性
        for c in constraints:
            for rel in c.relation_chain:
                validate_relation_constraint(rel, c.source_label, c.target_label)
        self._constraints = constraints

    def evaluate(self, entity_id: str, constraint_name: str) -> GuardDecision:
        """执行单个约束，返回 block/warn/allow + reason"""
```

### 4. ConstraintPropagator（execution/constraints/）

```python
class ConstraintPropagator:
    """独立 BFS 实现（不复用 ImpactPropagator，建模目标不同）"""
    def propagate(self, entity_id, along, property, strategy) -> PropagationResult:
        """沿指定关系链 BFS 收集属性值"""
```

### 5. ActionGuardPipeline（execution/constraints/）

```python
class ActionGuard(ABC):
    @abstractmethod
    def evaluate(config, entity, graph_store) -> GuardDecision: ...

class ActionGuardPipeline:
    def __init__(self, guards: list[ActionGuard]):
        self._guards = guards

    def check(self, config, entity, graph_store) -> str | None:
        for guard in self._guards:
            decision = guard.evaluate(config, entity, graph_store)
            if decision.level == "block":
                return decision.reason
        return None  # 通过

@dataclass
class GuardDecision:
    level: Literal["block", "warn", "allow"]
    reason: str
```

内置 guard：
- `EntityExistsGuard` — 替代 "entity exists"
- `EntityPropertyGuard` — 替代 "entity.lines > 100"  
- `OntologyTraversalGuard` — 执行 TraversalConstraint（Phase 5 新增）
- `OntologyPropagationGuard` — 执行 ConstraintPropagator（Phase 5 新增）

### 6. ActionExecutor 改造

```python
class ActionExecutor:
    def __init__(self, ..., guard_pipeline: ActionGuardPipeline | None = None):
        self._guard_pipeline = guard_pipeline

    def execute(self, intent_type, params):
        ...
        # 新路径优先
        if self._guard_pipeline:
            error = self._guard_pipeline.check(config, entity, self._graph_store)
        else:
            error = self._check_criteria(config, entity)  # 向后兼容
```

---

## 两周实施计划

### Week 1: 领域模型 + 引擎

**Day 1: 设计评审**
- [ ] 接口契约：TraversalConstraint / PropertyConstraint / CompoundConstraint 数据模型
- [ ] GuardDecision 语义定义（block/warn/allow 的行为差异）
- [ ] ActionGuardPipeline 接口
- [ ] ConstraintEngine 与 graph_store 的交互契约
- [ ] 确认 DataAsset/ComplianceItem 在 builder 中的构建路径（不用 --skip-semantic）

**Day 2-3: 领域模型 + ConstraintEngine**
- [ ] `domain/schema.py`: 新增 `TraversalConstraint`、`PropertyConstraint`、`CompoundConstraint`、`GuardDecision`
- [ ] `domain/schema.py`: `RelationConstraint` 加 `traverse_on_operation: bool = False`
- [ ] 新建 `execution/constraints/__init__.py`
- [ ] 新建 `execution/constraints/engine.py`: `ConstraintEngine`（≤200行）
- [ ] 加载时 `validate_relation_constraint()` 逐跳校验

**Day 4-5: ConstraintPropagator**
- [ ] 新建 `execution/constraints/propagator.py`: `ConstraintPropagator`（≤250行）
- [ ] 独立 BFS，沿指定关系链收集属性
- [ ] 支持 propagation_rules 配置：`{along: ["CALLS"], property: "risk_level", strategy: "max"}`
- [ ] 单元测试：
  - `tests/unit/test_constraint_engine.py` — mock graph_store
  - `tests/unit/test_constraint_propagator.py` — mock graph_store
- [ ] `uv run ruff check src/` + `uv run pytest tests/unit/ -q`

### Week 2: 集成

**Day 6-7: ActionGuardPipeline + ActionExecutor 改造**
- [ ] 新建 `execution/constraints/guard_pipeline.py`: `ActionGuardPipeline`
- [ ] 新建 `execution/constraints/guards.py`: 四个内置 guard
- [ ] `action_executor.py`: 注入 `guard_pipeline`，保留 `_check_criteria` 向后兼容
- [ ] `action_types.py`: `ActionConfig` 加 `guards: list[GuardConfig]` 可选字段
- [ ] `ontology_actions.yaml`: 为 refactor/document 配置 TraversalConstraint

**Day 8: 集成测试**
- [ ] `tests/integration/test_constraint_integration.py`
  - 构建场景：CodeEntity → DataAsset(restricted) → ComplianceItem(GDPR)
  - 验证 `EntityExistsGuard` + `EntityPropertyGuard` 行为不变
  - 验证 `OntologyTraversalGuard` — refactor 被 restricted 数据阻断
  - 验证 `OntologyPropagationGuard` — B 调用 A，A 处理 restricted → B refactor 被警告
- [ ] `uv run pytest tests/ -q` — 全量通过

**Day 9-10: Agent prompt + E2E 验证**
- [ ] `agent/prompt.py`: 追加约束感知（告知哪些 action 受本体约束）
- [ ] `intent_router.py`: `build_intent_prompt` 标注约束信息
- [ ] E2E 验证：
  ```bash
  uv run ontoagent build /opt/data/workspace/demo-service/ --clear
  uv run ontoagent ask "重构 validate_credit_card"
  # 预期: Agent 查 context → 发现 restricted → GuardDecision.block
  ```
- [ ] 缓冲：性能优化 / 文档 / 边界情况

---

## 明确不做

- 不引入新 DSL/语法 — 约束定义走 Python dataclass + YAML
- 不替换 RELATION_CONSTRAINTS — 增强，加载时交叉校验
- prompt 不参与判断 — 只告知约束存在
- 传播层不做衰减/权重模型 — BFS + 简单聚合策略
- 不做时序约束 — 架构可扩展但 Phase 5 不实现
- 不做自动派生 — 约束仍需手动定义（后续 Phase 可做）

---

## 关键设计决策

| 决策 | 理由 |
|------|------|
| TraversalConstraint 放 domain/ | 与 RelationConstraint 同质，都是值对象 |
| 约束组件放 execution/constraints/ 子包 | execution/ 已有18文件，避免突破15上限 |
| 独立 BFS（不复用 ImpactPropagator） | 建模目标不同：ImpactPropagator 做变更评分，ConstraintPropagator 做属性收集 |
| ActionGuardPipeline 替代字符串路由 | 字符串 DSL 不可扩展、无类型安全、违反开闭原则 |
| 加载时必须 validate_relation_constraint() | 这是"本体驱动"的闭环 — 约束定义受本体 schema 约束 |
| 保留 _check_criteria 向后兼容 | guard_pipeline 为 None 时 fallback 到旧逻辑 |

---

## 验证 demo-service 预期行为

```
用户: "重构 validate_credit_card"
Agent 内部:
  1. express_intent(refactor, validate_credit_card)
  2. ActionExecutor → GuardPipeline.check()
  3. EntityExistsGuard: PASS
  4. EntityPropertyGuard: lines=85 > 100? → PASS (不满足，跳过)
  5. OntologyTraversalGuard:
     → PROCESSES_DATA → 信用卡信息(sensitivity=restricted)
     → value_mapping: restricted=block
     → GuardDecision(level="block", reason="处理 restricted 级数据")
  6. OntologyPropagationGuard:
     → 反向 CALLS → PaymentHandler.process_charge(P0入口)
     → GuardDecision(level="warn", reason="被 P0 入口调用")
  7. Pipeline 合并: block > warn → 返回 block
  8. 告知用户并阻断操作
```
