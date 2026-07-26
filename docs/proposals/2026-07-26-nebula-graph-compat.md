# OntoAgent 图数据库多后端兼容方案（Neo4j → NebulaGraph）

> **状态：待 Claude Code 技术反思**
> **日期：2026-07-26**
> **目标：** 生产环境部署到只有 NebulaGraph 的环境，需要做底层适配

---

## 一、背景

OntoAgent 当前深度依赖 Neo4j（Bolt 协议 + Cypher 查询语言）。生产部署环境仅有 NebulaGraph，需做底层适配。

---

## 二、当前耦合深度（实查证据）

### 2.1 抽象层现状

**存在 `GraphStore` ABC**（`src/ontoagent/store/graph_store.py`，122 行，7 个抽象方法）：

```python
merge_node / get_node / delete_node
merge_relation / delete_relation / get_relations
query(cypher: str, params: dict) -> list[dict]   # ← 关键问题
cleanup_orphan_nodes
```

**致命缺陷：** `query()` 签名接受**裸 Cypher 字符串**。抽象层形同虚设。

### 2.2 耦合统计（grep 实测）

| 维度 | 数值 | 证据 |
|---|---|---|
| 含 Cypher 的源码文件 | **22 个** | `grep -l` 实测 |
| Cypher 语句总数 | **119 条** | `grep -rn "MATCH\|MERGE\|UNWIND\|DELETE"` |
| 直接 import Neo4jGraphStore | **15 处** | `_helpers.py`、`builder.py`、`cli.py`、`mcp_server.py`、`web/app.py`、`butler/handlers/base.py` 等 |
| MERGE 写操作 | 23 处 | 依赖 Neo4j 原生 MERGE 语义 |
| 变长路径 `*1..N` | 7 处 | 核心卖点"图遍历即权限" |
| UNWIND 批量写入 | 4 处 | `merge_nodes_batch`、`merge_relations_batch` |
| `labels(n)` 函数 | 多处 | Neo4j 专有 API（`graph.py` 统计/可视化） |

### 2.3 核心功能耦合分析

#### 🔴 高风险：ShapeEvaluator（约束引擎心脏）

```python
# execution/shape_evaluator.py:101-102
cypher = self._build_query(shape)           # PathExpression → Cypher
rows = self._graph_store.query(cypher, ...)
```

**PathCompiler**（`execution/path_compiler.py`）把约束规则编译成 Cypher：

```python
# 生成的 Cypher 形如
MATCH (n)-[:REL*1..3]->(collected:Label) WHERE n.id = $entity_id
RETURN collected.field AS val
```

→ **整个约束引擎的输出物就是 Cypher 字符串**。

#### 🔴 高风险：影响传播 & 业务追溯

```python
# execution/functions/builtin.py:89
MATCH (caller:CodeEntity)-[:CALLS*1..{depth}]->(callee:CodeEntity)

# execution/functions/trace_business_impact.py:21-22
MATCH path = (entry:CodeEntity)-[:CALLS*1..10]->(start)

# api/web/router/graph.py:43  (图可视化)
MATCH path = (center {name: $name}))-[*1..{depth}]-(neighbor)
```

#### 🟡 中风险：CRUD + 批量写入

`merge_node` 使用 `MERGE (n:Label {id: $id}) SET ...`
`merge_nodes_batch` 使用 `UNWIND $batch AS props MERGE ... SET n += props`

---

## 三、NebulaGraph vs Neo4j 关键差异

| 特性 | Neo4j 5.x | NebulaGraph 3.x | 迁移难度 |
|---|---|---|---|
| 查询语言 | Cypher（原生） | nGQL（3.x 起支持 openCypher MATCH 子集） | 🔴 高 |
| Schema 模型 | **无模式**（label 动态 MERGE） | **强 Schema**（必须先 `CREATE TAG` / `CREATE EDGE`） | 🔴 高 |
| 节点 ID | 自动生成或 MERGE 指定 | **必须指定 VID**（字符串或 int64） | 🟡 中 |
| MERGE 语义 | 原生支持 | **无 MERGE**，需 `INSERT VERTEX IF NOT EXISTS` 或应用层 upsert | 🔴 高 |
| 变长路径 `*1..N` | 原生高效 | 支持 `MATCH`+`-[e:EDGE*1..3]->` 但性能模型不同 | 🟡 中 |
| `labels(n)` 函数 | 原生 | 不同 API：`YIELD` + Tag 查询 | 🟡 中 |
| UNWIND 批量 | 原生 | 有限支持，语法不同 | 🟡 中 |
| 事务模型 | ACID 事务 | **无完整 ACID**（最终一致性） | 🔴 高（影响 MERGE 幂等） |
| 属性命名 | camelCase 动态 | Tag schema 预定义字段 | 🟡 中 |

### 最致命的差异

1. **强 Schema**：NebulaGraph 必须先 DDL 建模 Tag/Edge，才能插入数据。OntoAgent 当前 9 实体 13 关系全是运行时动态 MERGE，没有 DDL 步骤。
2. **无 MERGE**：NebulaGraph 只有 `INSERT VERTEX IF NOT EXISTS`（有限幂等），应用层需自己实现 upsert 逻辑。
3. **V2.3+ openCypher 子集**：NebulaGraph 支持 `MATCH` 但不支持 `MERGE` / `UNWIND` / `labels()` / `shortestPath`。

---

## 四、三条可选路线

### 路线 A：双后端共存（推荐）

保留 Neo4j 为默认后端，新增 `NebulaGraphStore` 作为可选后端。配置切换。

**核心改造：**
1. **重构 `GraphStore` 接口**：去掉 `query(cypher)`，换成语义化方法（或保留但标记 deprecated）
2. **引入查询 IR 层**：扩展现有 `PathExpression` 作为统一中间表示
3. **各后端把 IR 翻译成自己的查询语言**：
   - Neo4j: `PathCompiler` → Cypher（已有）
   - NebulaGraph: 新增 `NebulaQueryCompiler` → nGQL

**优势：** 已有 PathExpression IR，不用从零设计。
**风险：** 上层 22 个文件的 Cypher 要逐步迁移到语义化 API。

### 路线 B：彻底去 Cypher 化

119 条 Cypher 全部重写为语义化 API 调用。抽象层完全封闭查询语言。

**优势：** 最干净。
**风险：** 工程量最大，核心代码全面手术。

### 路线 C：用 NebulaGraph openCypher 兼容层

直接把 Cypher 微调喂给 NebulaGraph。

**致命风险：** MERGE 不支持、UNWIND 有限、强 Schema 要求、`labels()` 不可用。不推荐。

---

## 五、推荐路线 A 的分阶段计划

### Phase 1：抽象层重构（不改业务代码）
- 扩展 `GraphStore` 接口，新增语义化查询方法
- 定义统一的查询 IR（基于 PathExpression 扩展）
- 保留 `query(cypher)` 为 deprecated 后门，供过渡期使用

### Phase 2：NebulaGraph 后端实现
- 实现 `NebulaGraphStore(GraphStore)`
- 实现 `NebulaSchemaInitializer`（从 schema.py 自动生成 Tag/Edge DDL）
- 实现 `NebulaQueryCompiler`（IR → nGQL）

### Phase 3：上层 Cypher 迁移
- 22 个文件的 119 条 Cypher 逐步替换为语义化 API
- 高风险模块优先：ShapeEvaluator、影响传播、业务追溯
- 低风险模块（CRUD）最后处理

### Phase 4：测试 & 切换
- 双后端集成测试（真实 NebulaGraph 实例）
- 配置切换：`ONTOAGENT_GRAPH_BACKEND=nebula`

---

## 六、需要 Claude Code 反思的核心问题

1. **双后端共存的抽象层设计是否合理？** 接口该怎么设计才能同时满足 Cypher 和 nGQL 两种查询语言？

2. **PathExpression 作为统一 IR 是否够用？** 当前 PathExpression 主要服务 ShapeEvaluator（单跳/多跳路径查询），能否覆盖影响传播（双向 BFS）、图可视化（任意关系邻居展开）等场景？

3. **ShapeEvaluator 的编译器模式如何改造？** PathCompiler 输出 Cypher 字符串。改造方向：
   - 选项 1：PathCompiler 输出 IR 对象，各后端再翻译
   - 选项 2：保留 PathCompiler 输出 Cypher，新增 NebulaPathCompiler 输出 nGQL（代码重复）
   - 哪个更好？

4. **NebulaGraph 强 Schema 如何处理？** 9 实体 13 关系需要预定义 Tag/Edge。方案：
   - 选项 1：从 `schema.py` 的 RELATION_TYPE_TO_NEO4J 映射自动生成 DDL
   - 选项 2：新增 `NebulaSchemaInitializer` 手动维护
   - 动态新增本体（用户自定义实体/关系）时，强 Schema 是否会破坏"本体驱动"的灵活性？

5. **NebulaGraph 无 MERGE 的影响？** OntoAgent 大量使用 MERGE 做幂等写入。改用 `INSERT IF NOT EXISTS` + 应用层 upsert 是否可靠？并发写入场景？

6. **是否有我未考虑到的风险或更好的替代方案？** 比如：
   - 是否应该用 Apache TinkerPop（Gremlin）作为统一抽象？
   - 是否应该评估其他图数据库（Memgraph 兼容 Cypher，换底层更简单）？
   - 性能差异是否会影响"图遍历即权限"的实时性？

---

## 七、关键代码位置索引（供审查参考）

| 关注点 | 文件 | 行号 |
|---|---|---|
| GraphStore 抽象 | `src/ontoagent/store/graph_store.py` | 全文 122 行 |
| Neo4j 实现 | `src/ontoagent/store/neo4j_store.py` | 572 行 |
| PathCompiler（Cypher 生成器） | `src/ontoagent/execution/path_compiler.py` | 114 行 |
| ShapeEvaluator | `src/ontoagent/execution/shape_evaluator.py` | 211 行 |
| 影响传播 | `src/ontoagent/pipeline/impact_propagator.py` | 436 行 |
| 图可视化 API | `src/ontoagent/api/web/router/graph.py` | 166 行 |
| Schema 定义 | `src/ontoagent/domain/schema.py` | RELATION_TYPE_TO_NEO4J 映射 |
