# Phase 2-5 实施计划：评估引擎 → 集成 → Agent 感知 → 清理

> **审查**: Claude Code | **日期**: 2026-06-30

---

## Phase 2：评估引擎

### PathCompiler (`execution/path_compiler.py`)

**防注入策略**（Claude Code 强制要求）：

1. 属性名/关系类型/Label 三类标识符**必须通过白名单**（ONTOLOGY_ENTITY_LABELS + ONTOLOGY_RELATION_TYPES + VALID_PROPERTY_RE）精确匹配后才能内联拼入 Cypher
2. 路径算子（`/`、`|`、`*`、`+`、`^`）是编译期 token，由 switch 生成 Cypher 片段，不来自输入
3. 输入做 NFKC 正规化 + 去零宽字符
4. **绝不** `f"WHERE n.{prop} = ..."`，改为 `n[$prop] = $val`

**Cypher 生成规则**：

```
单跳:     CALLS                 → -[:CALLS]->(b)
变长:     CALLS+                → -[:CALLS*1..{max_depth}]->(b)
限长:     CALLS{1,3}            → -[:CALLS*1..3]->(b)
反向:     ^CALLS                → <-[:CALLS]-(b)
序列:     CALLS / IMPLEMENTS    → -[:CALLS]->(m)-[:IMPLEMENTS]->(b)
SELF:     SELF                  → (不产生遍历，直接在源节点 eval)
```

### ConstraintExpr → Cypher WHERE

```
equals   → n[$prop] = $val          (值走参数)
in       → n[$prop] IN $vals        (列表走参数)
exists   → n[$prop] IS NOT NULL
not_in   → NOT n[$prop] IN $vals
not_equals → n[$prop] <> $val
```

### ShapeEvaluator (`execution/shape_evaluator.py`)

```
1. 输入: entity_id + capabilities [(resource, operation), ...]
2. ShapeRegistry.find(resource, operation) → 候选 Shape 列表
3. 对每个 Shape:
   a. PathCompiler.compile(shape.path) → (cypher, params)
   b. Neo4j 查询 → 终点节点列表
   c. 对每个终点节点 eval ConstraintExpr → 匹配则记录 evidence
4. 返回: [(Shape, Severity, evidence, triggered_nodes), ...]
```

### DecisionFuser (`execution/decision_fuser.py`)

**融合策略**（Claude Code 强制要求）：

1. 严格优先：BLOCK > ESCALATE > WARN > ALLOW
2. Shape.priority 覆盖同级别：高 priority 的同级 Shape 的 suggestion 优先采用
3. 同 priority 冲突：取交集；无法交集 → **升级到 ESCALATE**，携带冲突报告
4. **缺少 priority 字段 → 强制 ESCALATE**（禁止隐式优先级）
5. 输出 DecisionReport：`{decision, triggered_shapes, fused_suggestion, conflict_report?}`

### 最大风险

PathCompiler 的**反向边 + 变长路径组合**编译到 Cypher 时极易语义偏移（cartesian product + 静默漏匹配）。需要 golden case 对拍（同一 Shape 在手动 Cypher vs PathCompiler 输出上跑相同 KG fixture，断言结果集合相等）。

---

## Phase 3：集成执行链路

### 执行顺序（Claude Code 强制）

```
3a: Function YAML化          ← 先做（机械重构，独立性最高）
3b: Submission Criteria 迁移  ← 次做（建立 Shape 为唯一约束来源）
3c: 替换 Guard Pipeline       ← 最后（此时 Shape 已汇集全部约束）
```

**Feature flag**: `ONTOAGENT_SHAPE_EVALUATOR_ENABLED`，允许新旧双跑 diff 后再硬切。

### 3a: Function YAML 化

- 现有 `execution/functions/registry.py` 的 dict → `functions.yaml`
- 给每个 Function 加 `capabilities` 字段：格式 `"CodeEntity:UPDATE"`（字符串，延迟解析，避免循环引用）
- FunctionRunner 加载路径改为从 YAML

### 3b: Submission Criteria 迁移

- 现有 `entity.lines > 100` 等字符串表达式 → Shape（kind=STRUCTURAL, constraint: {field: lines, operator: ">", value: 100}）
- **关键风险**：求值时机可能偏移（resolve 前 vs resolve 后）。保留旧 Criteria 实现做 schema-by-schema diff，diff=0 才删。

### 3c: 替换 Guard Pipeline

- `ActionExecutor._check_criteria` → 调 ShapeEvaluator
- 删除 `ontology_actions.yaml` 所有 `guard_configs`
- `graph_query` 白名单已有（`tool_gateway.py:41`），仅补测试

---

## Phase 4：Agent 感知

### 三个新工具

| 工具 | 输入 | 输出 | 实现 |
|------|------|------|------|
| `explore_ontology()` | — | 实体类型 + 关系类型 + Shape 摘要（name+desc） | ShapeRegistry.list_all() → 过滤 enabled=true → 精简 |
| `explain_constraint(id)` | shape_id | Shape 全文 + path + constraint + 最近触发 | ShapeRegistry.get(id) |
| `suggest_alternatives(intent, target)` | intent_type + entity | top-3 替代方案 + 解释 | 预演过滤 → Jaccard 相似度打分 → 截断 |

### suggest_alternatives 算法

```
1. 候选集: 同 target 资源类型的所有 intent_type
2. 预演过滤: 用候选 Shape 在当前 entity 上预演评估，剔除同样会失败的
3. 打分: score = Jaccard(failed_capabilities, candidate_capabilities)
4. 去重 + 截断: top-3，附解释
```

### Prompt 重写

- 从 ShapeRegistry 生成，仅 name + description 摘要
- 按当前 intent_type 做**相关性排序后截断**（不是按字典序）
- ≤200 token 预算

### suggestion 去重/排序

多 Shape BLOCK 时，按 priority 降序取 suggestion。同 priority 冲突 → 升级 ESCALATE，不静默丢弃。

---

## Phase 5：清理 + 测试 + 文档

### 删除清单

| 删除 | 原因 |
|------|------|
| `ONTOLOGY_CONSTRAINT_REGISTRY` | Phase 1 已冻结为空 |
| `ConstraintFieldDescriptor` | 被 ConstraintShape 取代 |
| 旧 Guard Pipeline（5 个 Guard） | 被 ShapeEvaluator 取代 |
| 旧 `constraints.yaml` | 被 shapes.yaml 取代 |
| `OntologyConstraintLoader` | 被 ShapeRegistry 取代 |

### constraint_overrides.yaml 命运：**废弃**

- V4 Shape 本身就是声明式约束，再加 overrides 形成双源真理
- "临时关闭约束" → Shape.enabled = false
- "针对某实体放宽" → Phase 6 做 Shape 的 entity_id 维度 override clause

### 迁移文档

- V3 → V4 升级指南（guard_configs 怎么搬到 shapes.yaml）
- `ontoagent migrate-shapes` CLI 命令（自动迁移脚本）

---

## 全局风险矩阵

| 风险 | 阶段 | 等级 | 对策 |
|------|:---:|:---:|------|
| Criteria 迁移语义偏移 | 3b | 🔴 | 新旧 diff，diff=0 才删 |
| PathCompiler 路径组合语义错误 | 2 | 🔴 | golden case 对拍 |
| 全量替换无灰度 | 3c | 🟠 | feature flag + 双跑 diff |
| Shape 数量增长后 prompt 截断丢约束 | 4 | 🟡 | 按 intent relevance 排序截断 |
| Function YAML 循环引用 | 3a | 🟡 | 字符串延迟解析 |
| Double BLOCK suggestion 冲突 | 2 | 🟡 | priority → 交集 → ESCALATE |
