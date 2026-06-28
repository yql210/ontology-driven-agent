# Phase 5: 本体注解驱动约束框架

## 目标

将本体定义提升为 agent 行为约束的驱动源。框架提供通用的"本体注解→约束推导"能力，不绑定特定业务场景。demo-service 的 DataAsset/ComplianceItem 只是验证框架能力的示例本体。

## 架构

```
ontology_actions.yaml          schema.py (RelationConstraint)
       │                              │
       │  submission_criteria         │  traverse_on_operation
       │  + check_constraint:*        │  + propagate_along
       ▼                              ▼
┌─────────────────────────────────────────────┐
│          ConstraintEngine                   │
│  ┌─────────────────────────────────────┐   │
│  │  check_data_sensitivity(id)         │   │  ← 一跳检查（结构层）
│  │  check_compliance_chain(id)        │   │
│  └─────────────────────────────────────┘   │
│  ┌─────────────────────────────────────┐   │
│  │  ConstraintPropagator               │   │  ← 多跳传播（传播层）
│  │  propagate(along, property, strat) │   │
│  └─────────────────────────────────────┘   │
└─────────────────────────────────────────────┘
       │
       ▼
  ActionExecutor._check_criteria()
       │
       ▼
  Agent ← 阻断/警告/允许 + 理由
```

## 三层能力

### 结构层（一跳检查）
从实体/关系定义 + 关系注解推导。框架不关心 DataAsset 是什么——只关心"此关系标记了 traverse_on_operation=true"。

| 检查 | 查询路径 | 返回值 |
|------|---------|--------|
| data_sensitivity | CodeEntity → PROCESSES_DATA → DataAsset.sensitivity | allow/warn/block |
| compliance_chain | CodeEntity → (SUBJECT_TO\|PROCESSES_DATA→GOVERNED_BY) → ComplianceItem | allow/warn/block |

### 语义层（属性注解驱动）
框架不关心 sensitivity 的业务含义——只关心属性配置了 constraint_level 元数据。

```python
@dataclass
class PropertyConstraintMeta:
    field: str           # "sensitivity"
    per_value: dict[str, str]  # {"restricted": "block", "confidential": "warn", ...}
```

### 传播层（多跳传递）
基于现有 ImpactPropagator 的 BFS 骨架，沿指定关系链传播约束属性。

```python
class ConstraintPropagator:
    def propagate(entity_id, along=["CALLS"], property="risk_level", strategy="max"):
        """BFS 沿关系链收集属性值，按策略聚合返回最高约束级别。"""
```

| 传播规则 | 语义 | 示例 |
|---------|------|------|
| along CALLS, property=risk_level, strategy=max | 调用链风险继承 | A→B→C，A处理restricted数据，C间接受影响 |
| along CALLS (反向), strategy=exists | 反向找入口点 | 被3个P0入口调用 |

---

## 两周实施计划

### Week 1: 约束引擎 + 传播器

**Day 1-2: 基础结构**
- [ ] `src/ontoagent/domain/schema.py`: `RelationConstraint` 加 `traverse_on_operation: bool = False`
- [ ] 新建 `src/ontoagent/domain/constraint_meta.py`: `PropertyConstraintMeta` dataclass
- [ ] 新建 `src/ontoagent/execution/constraints.py`: `ConstraintEngine` 骨架（≤300行）
  - `check_data_sensitivity(entity_id, graph_store)` → BFS 沿 PROCESSES_DATA
  - `check_compliance_chain(entity_id, graph_store)` → 沿 SUBJECT_TO + GOVERNED_BY

**Day 3-4: 传播器**
- [ ] 新建 `src/ontoagent/execution/constraint_propagator.py`: `ConstraintPropagator`（≤250行）
  - 复用 `ImpactPropagator` 的 BFS 遍历模式
  - `propagate(entity_id, along, property, strategy)` → 返回聚合约束级别
  - `find_entry_points(entity_id)` → 反向 CALLS 找入口

**Day 5: 单元测试**
- [ ] `tests/unit/test_constraint_engine.py` — mock graph_store，测一跳检查
- [ ] `tests/unit/test_constraint_propagator.py` — mock graph_store，测传播
- [ ] `uv run ruff check src/` + `uv run pytest tests/unit/ -q`

### Week 2: 集成

**Day 6-7: ActionExecutor 集成**
- [ ] `action_executor.py::_check_criteria` 扩展 `check_constraint:` 路由
  ```python
  elif criterion.startswith("check_constraint:"):
      constraint_type = criterion.split(":", 1)[1]
      error = self._check_ontology_constraint(entity, constraint_type)
      if error: return error
  ```
- [ ] `ActionExecutor.__init__` 注入 `ConstraintEngine`
- [ ] `ontology_actions.yaml` 为 refactor/compliance_check/document 加 constraint

**Day 8: 集成测试**
- [ ] `tests/integration/test_constraint_integration.py`
  - 构建场景：CodeEntity → DataAsset(restricted) → ComplianceItem(GDPR)
  - 验证 refactor 被阻断
  - 验证传播：B 调用 A，A 处理 restricted 数据 → B 的 refactor 被警告
- [ ] `uv run pytest tests/ -q` — 全量通过

**Day 9-10: Agent prompt + 端到端验证**
- [ ] `agent/prompt.py::AGENT_SYSTEM_PROMPT` 追加约束提示
- [ ] `intent_router.build_intent_prompt()` 标注哪些 action 会触发约束
- [ ] E2E 验证：
  ```bash
  uv run ontoagent build /opt/data/workspace/demo-service/ --clear --skip-semantic
  uv run ontoagent ask "重构 validate_credit_card"
  # 预期: Agent 先查 get_context → 发现 restricted 数据 → 警告用户
  ```

---

## 明确不做

- 不引入新 DSL/语法——约束注解走 Python dataclass + YAML
- 不替换 RELATION_CONSTRAINTS——增强而非替代
- 不修改 prompt 里的硬规则——prompt 只告知约束存在，不参与判断
- 传播层不做衰减/权重模型——复用现有 ImpactPropagator 的简单 BFS

---

## 验证 demo-service 预期行为

```
用户: "重构 validate_credit_card"
Agent 内部:
  1. get_context(validate_credit_card)
  2. 发现 PROCESSES_DATA → 信用卡信息(sensitivity=restricted) 
     → ConstraintEngine: block
  3. 发现反向 CALLS → PaymentHandler.process_charge(P0入口)
     → ConstraintPropagator: warn
  4. 综合: block > warn → 返回 block
  5. 告知用户: "此函数处理 restricted 级数据(信用卡信息)，受 PCI-DSS-3.4 约束，且被 P0 入口调用。建议先做合规评估。"
```
