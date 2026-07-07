# OntoAgent 全面验证方案

> **日期：** 2026-07-07
> **背景：** entry_type × ontology_ref 架构拆分完成后，需要对新功能（本体加载链路）和存量功能（约束执行链路）做完整的端到端验证，确保"可以直接上生产测试"。
> **教训：** 此前仅做了 `validate-shapes`（结构校验），未跑 ShapeEvaluator 运行时评估，虚报"跑通了"。本文档防止重蹈覆辙。

---

## 一、系统全链路总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                        完整链路（6 层）                                │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Layer 1: 本体生成                                                   │
│  DDL(.sql) → OntologyAutoGen → ontology.json                        │
│  ✅ 电商域已跑通（44KB, 69 实体）                                      │
│                                                                     │
│  Layer 2: Shape 转换                                                 │
│  ontology.json → ontology_loader.py → shapes.yaml                   │
│  ✅ 69 条 Shape 生成，entry_type=ResourceEntity, ontology_ref=域名   │
│                                                                     │
│  Layer 3: Shape 注册与校验                                            │
│  shapes.yaml → ShapeRegistry.load_from_yaml → validate_shape         │
│  ✅ 69/69 校验通过（结构校验 + cross_shape 字段校验）                   │
│                                                                     │
│  Layer 4: 图数据构建                                                  │
│  源代码/DDL → builder.py → Neo4j 节点+关系                           │
│  ⚠️ 存量 demo-service 有图数据；电商域无图数据                         │
│                                                                     │
│  Layer 5: 运行时约束评估                                              │
│  entity + operation → ShapeEvaluator → ShapeResult → DecisionFuser   │
│  ❌ 电商域从未执行过；存量 demo-service 有集成测试但需 Neo4j            │
│                                                                     │
│  Layer 6: Agent 端到端                                               │
│  自然语言 → express_intent → ActionExecutor → _check_with_shapes     │
│      → (BLOCK / WARN / ESCALATE → ApprovalGate)                     │
│  ❌ 电商域从未执行过；存量 demo-service 有集成测试但需 Neo4j            │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 当前状态总结

| 层 | 新功能（电商域） | 存量功能（demo-service） |
|----|-----------------|------------------------|
| L1 本体生成 | ✅ | ✅（内置 shapes.yaml） |
| L2 Shape 转换 | ✅ | ✅（手写 shapes.yaml） |
| L3 Shape 校验 | ✅ 69/69 | ✅ 5/5 |
| L4 图数据 | ❌ 无图 | ⚠️ 需 build demo-service |
| L5 运行时评估 | ❌ 未跑 | ⚠️ 有集成测试（需 Neo4j） |
| L6 Agent E2E | ❌ 未跑 | ⚠️ 有集成测试（需 Neo4j） |

---

## 二、验证方案（6 个 Phase）

每个 Phase 必须用真实工具输出证明通过，不接受"应该可以"。

---

### Phase 0: 存量回归（确保拆分没破坏老功能）

**目标：** entry_type 重命名没有破坏任何已有功能。

**验证项：**

| # | 验证内容 | 命令 | 预期 |
|---|---------|------|------|
| 0.1 | 全量单元测试 | `uv run pytest tests/ -q` | 1714+ passed, 0 failed |
| 0.2 | ruff 静态检查 | `uv run ruff check src/ tests/` | 0 new errors |
| 0.3 | 存量 shapes.yaml 校验 | `uv run ontoagent validate-shapes` | Validated 5 shapes |
| 0.4 | 存量 shapes.yaml 含 entry_type | `grep entry_type src/ontoagent/pipeline/shapes.yaml` | 5 处 |
| 0.5 | 旧格式 resource_type 兼容 | `test_from_yaml_dict_legacy_resource_type_compat` | PASS |
| 0.6 | ontology_ref 序列化 | `test_explain_constraint_returns_full_shape` 中 `data["target"]["entry_type"]` | PASS |

**通过标准：** 全部 ✅。任何一项失败 = 拆分引入了回归，必须修。

---

### Phase 1: 本体生成链路（L1-L2）

**目标：** DDL → ontology.json → shapes.yaml 链路完整，产出格式正确。

**验证项：**

| # | 验证内容 | 命令 | 预期 |
|---|---------|------|------|
| 1.1 | 电商 ontology.json 存在 | `ls -la /tmp/OntologyAutoGen/.../output/ontology.json` | 44KB |
| 1.2 | ontology_loader 执行 | `uv run python -m ontoagent.pipeline.ontology_loader <input> <output>` | "已生成 69 条 Shape" |
| 1.3 | entry_type 全部是标准标签 | `grep "entry_type:" /tmp/ecommerce_v3.yaml \| sort \| uniq -c` | 只有 ResourceEntity |
| 1.4 | ontology_ref 包含域语义 | `grep "ontology_ref:" /tmp/ecommerce_v3.yaml \| head -5` | 客户/商品/订单等 |
| 1.5 | version 标记 | `head -1 /tmp/ecommerce_v3.yaml` | version: '2.1' |
| 1.6 | severity 分布合理 | `grep severity /tmp/ecommerce_v3.yaml \| sort \| uniq -c` | warn/allow/block 三种 |

**通过标准：** 全部 ✅。

---

### Phase 2: Shape 注册与校验（L3）

**目标：** 69 条 Shape 全部通过 ShapeRegistry 的双重校验。

**验证项：**

| # | 验证内容 | 命令 | 预期 |
|---|---------|------|------|
| 2.1 | validate-shapes 命令 | `uv run ontoagent validate-shapes --path /tmp/ecommerce_v3.yaml` | Validated 69 shapes, 0 errors |
| 2.2 | validate_shape（标签校验） | 上述输出无 "不在合法标签集合" | 0 条标签错误 |
| 2.3 | validate_cross_shape（字段校验） | 上述输出无 "不在字段集中" | 0 条字段错误 |
| 2.4 | 倒排索引正确 | Python: `registry.get_shapes("ResourceEntity", Operation.UPDATE)` 返回 69 条 | len == 69 |

**通过标准：** 69/69 通过，0 error。

---

### Phase 3: 图数据构建（L4）

**目标：** 在 Neo4j 中构建电商域的图节点，供 ShapeEvaluator 查询。

**前置条件：** Neo4j 可连接（`docker compose up -d`）。

**验证项：**

| # | 验证内容 | 方法 | 预期 |
|---|---------|------|------|
| 3.1 | Neo4j 连接 | `uv run pytest tests/integration/test_neo4j_connection.py -v` | PASS |
| 3.2 | 电商域节点写入 | 手动 CREATE 或脚本写入 ResourceEntity 节点（name="客户"/"订单"/"商品"等） | 节点数 ≥ 5 |
| 3.3 | 节点查询验证 | `MATCH (n:ResourceEntity) RETURN n.name, n.entityType` | 返回电商实体 |
| 3.4 | 关系写入 | 创建 HAS_CUSTOMER / HAS_PRODUCT 等关系 | 关系数 ≥ 3 |
| 3.5 | 关系查询验证 | `MATCH (n:ResourceEntity {name:"订单"})-[:HAS_CUSTOMER]->(m) RETURN m.name` | 返回"客户" |

**通过标准：** Neo4j 中有电商域的节点和关系，可被 Cypher 查询到。

> **注意：** 这是当前最大的缺口——电商域没有图数据。需要决定：手动建图，还是扩展 builder 支持从 DDL 直接建图。

---

### Phase 4: 运行时约束评估（L5）—— 核心验证

**目标：** ShapeEvaluator 在真实图数据上执行，约束按预期触发/不触发。

**这是此前完全缺失的验证层。**

**验证项：**

| # | 验证内容 | 方法 | 预期 |
|---|---------|------|------|
| 4.1 | SELF 路径 Shape 触发 | 对 ResourceEntity(name="客户") 执行 UPDATE，评估 shape:entity_concept_bd070df1 | triggered=True, severity=warn |
| 4.2 | SELF 路径 Shape 不触发 | 对 ResourceEntity(name="不存在") 执行 UPDATE | triggered=False |
| 4.3 | relation 路径 Shape 触发 | 对有 HAS_CUSTOMER 关系的节点执行 UPDATE | triggered=True |
| 4.4 | effective_severity 降级 | 对 confidence=0.63 的 Shape，验证 effective_severity 从 warn→allow | effective != severity |
| 4.5 | DecisionFuser 聚合 | 多条 Shape 同时触发，验证 BLOCK 优先于 WARN | severity=BLOCK |
| 4.6 | allow_set 短路 | 白名单实体跳过所有 Shape | results=[] |
| 4.7 | path 表达式编译 | PathCompiler.compile 对 "HAS_CUSTOMER -> ResourceEntity" 生成正确 Cypher | MATCH (n)-[:HAS_CUSTOMER]->(collected:ResourceEntity) |

**验证方法（Python 脚本）：**

```python
from ontoagent.execution.shape_evaluator import ShapeEvaluator
from ontoagent.execution.shape_registry import ShapeRegistry
from ontoagent.domain.shapes import Operation

registry = ShapeRegistry(valid_labels={...})
registry.load_from_yaml("/tmp/ecommerce_v3.yaml")
evaluator = ShapeEvaluator(registry, graph_store)

# 4.1: SELF 路径触发
entity = {"id": "node-001", "labels": ["ResourceEntity"], "name": "客户"}
results = evaluator.evaluate(entity, [Operation.UPDATE])
assert any(r.triggered for r in results), "应该有 Shape 被触发"

# 4.2: 不触发
entity = {"id": "node-002", "labels": ["ResourceEntity"], "name": "不存在"}
results = evaluator.evaluate(entity, [Operation.UPDATE])
assert not any(r.triggered for r in results), "不应该触发"

# 4.4: effective_severity
for r in results:
    if r.triggered and r.shape.confidence < 0.9:
        assert r.severity != r.shape.severity, "应该降级"
```

**通过标准：** 每一项用真实 graph_store.query() 输出验证，不接受 mock。

---

### Phase 5: Agent 端到端（L6）

**目标：** 完整的 Agent → ActionExecutor → ShapeEvaluator → 决策 链路。

**验证项：**

| # | 验证内容 | 方法 | 预期 |
|---|---------|------|------|
| 5.1 | ActionExecutor.execute 触发 Shape 检查 | `executor.execute("refactor", {"target": "客户节点"})` | 调用 _check_with_shapes |
| 5.2 | BLOCK 场景 | Shape severity=block 且触发 | result.success=False, error 含 block |
| 5.3 | WARN 场景 | Shape severity=warn 且触发 | result.success=True, warnings 非空 |
| 5.4 | ESCALATE → 审批 | Shape severity=escalate 且触发 | warnings 含 "approval_required" |
| 5.5 | explain_constraint 工具 | `explain_constraint("shape:entity_concept_bd070df1")` | 返回含 entry_type + ontology_ref 的 JSON |
| 5.6 | list_shapes 工具 | `list_shapes()` | 返回 69 条 Shape |

**通过标准：** 真实 ActionExecutor + 真实 graph_store，不接受 mock。

---

### Phase 6: 存量集成测试（demo-service）

**目标：** 确保 entry_type 拆分没有破坏 demo-service 的约束链路。

**前置条件：** demo-service 已 build 到 Neo4j。

**验证项：**

| # | 验证内容 | 命令 | 预期 |
|---|---------|------|------|
| 6.1 | 敏感数据 BLOCK | `test_refactor_validate_credit_card_blocked` | BLOCK (success=False) |
| 6.2 | 无关联数据 ALLOW | `test_refactor_daily_reconciliation_allowed` | ALLOW (不被 Shape 阻断) |
| 6.3 | 操作不匹配 ALLOW | `test_compliance_check_validate_credit_card_allowed` | ALLOW |
| 6.4 | Registry 已注入 | `test_executor_shape_registry_is_wired` | registry ≥ 4 shapes |

**通过标准：** 4 条集成测试全 PASS（需要 Neo4j + demo-service 已 build）。

---

## 三、当前已确认的语义问题

这些问题在 Phase 4 验证中会暴露，需提前处理：

### 问题 A: constraint.field = "name" 是权宜之计

**现状：** ontology_loader 把 entity_type shape 的 `constraint.field` 设为 `"name"`，因为 ResourceEntity 有 `name` 字段。

**问题：** 这意味着 ShapeEvaluator 会查 `n.name` 属性，用 `operator=eq` 匹配 `value=["客户"]`。只有当图里 ResourceEntity 的 `name` 恰好等于"客户"时才触发。

**需要验证：** 图里的 ResourceEntity 的 `name` 存的是什么？是表名（`customer`）还是业务名（`客户`）？如果是表名，Shape 的 value 需要改为表名，或者 ResourceEntity 需要加一个 `ontologyRef` 属性。

### 问题 B: relation shape 的 path 终点丢失语义精度

**现状：** `path: "HAS_CUSTOMER -> ResourceEntity"` 指向任意 ResourceEntity。

**问题：** 原来指向具体概念（`-> 客户`），现在指向所有 ResourceEntity。可能误触发。

### 问题 C: ontology_ref 未被运行时消费

**现状：** `ontology_ref` 在 ShapeEvaluator 中完全没有被读取。

**问题：** 如果 ontology_ref 不参与运行时匹配，拆分这个字段的实际价值仅限于校验通过。要让它在运行时有意义，ShapeEvaluator 需要在 _should_trigger 中用 ontology_ref 做二次过滤。

---

## 四、验证执行顺序

```
Phase 0 (存量回归)
  ↓ PASS
Phase 1 (本体生成) ← 已完成
  ↓ PASS
Phase 2 (Shape 校验) ← 已完成
  ↓ PASS
Phase 3 (图数据构建) ← 需要做
  ↓ PASS
Phase 4 (运行时评估) ← 核心缺口
  ↓ PASS
Phase 5 (Agent E2E) ← 需要做
  ↓ PASS
Phase 6 (存量集成) ← 需要 Neo4j
  ↓ PASS
→ 可以上生产测试
```

**任何一层失败，停下修复后再继续。不跳过。**

---

## 五、判断"可以上生产"的硬标准

| # | 标准 | 当前状态 |
|---|------|---------|
| 1 | 全量单元测试 0 失败 | ✅ 1714 passed |
| 2 | 电商 Shape 69/69 校验通过 | ✅ |
| 3 | 电商域图数据存在于 Neo4j | ❌ |
| 4 | ShapeEvaluator 在真实图上触发正确 | ❌ |
| 5 | effective_severity 降级被验证 | ❌ |
| 6 | ActionExecutor 端到端 BLOCK/WARN/ESCALATE | ❌ |
| 7 | 存量 demo-service 集成测试通过 | ⚠️ 需 Neo4j |
| 8 | explain_constraint 返回含 ontology_ref | ⚠️ 需验证 |
| 9 | 代码安全域（第二域）跑通 | ❌ |

**当前：2/9 通过。距离生产可用还有 7 项。**
