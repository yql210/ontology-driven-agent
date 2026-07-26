# OntoAgent 图数据库多后端兼容方案 V2（修订版）

> **状态：已通过 Claude Code 技术反思，待用户确认**
> **日期：2026-07-26**
> **前置决策：生产环境强制使用 NebulaGraph（客户/运维指定，无选择余地）**
> **修订说明：已修正 V1 中 3 个事实性错误，纳入 CC 审查的 6 项核心判定**

---

## 一、背景

OntoAgent 当前深度依赖 Neo4j（Bolt + Cypher）。生产部署环境仅有 NebulaGraph，需做底层适配。

---

## 二、耦合深度（实查数据，已修正）

### 2.1 事实修正（V1 错误纠正）

| 维度 | V1（错误） | V2（实查正确） | 证据 |
|---|---|---|---|
| 实体数量 | 9 | **13** | `schema.py:506-522` VALID_ENTITY_LABELS |
| 关系数量 | 13 | **26** | `schema.py:582-644` VALID_RELATION_TYPES + RELATION_TYPE_TO_NEO4J |
| ImpactPropagator | 列为高风险 | **低风险**——核心 `_bidirectional_bfs` 用语义化 `get_relations()` API，不用 Cypher 变长路径 | `impact_propagator.py:312-314` |
| PathCompiler 输出 | "整个约束引擎输出物" | 只输出 MATCH 子句片段；WHERE + RETURN 在 ShapeEvaluator 里拼接 | `path_compiler.py:64`、`shape_evaluator.py:131-152` |

### 2.2 修正后的真实耦合统计

| 维度 | 数值 | 说明 |
|---|---|---|
| 含 Cypher 的源码文件 | 22 个 | |
| Cypher 语句总数 | 119 条 | |
| 直接 import Neo4jGraphStore | 15 处 | |
| MERGE 写操作 | 23 处 | |
| 变长路径 `*1..N` | 7 处 | |
| 走语义化 API（非 Cypher）的模块 | ImpactPropagator 核心 BFS | **好消息：影响传播模块迁移成本低** |

### 2.3 真正的高风险区域（修正后）

| 模块 | 风险 | 原因 |
|---|---|---|
| ShapeEvaluator + PathCompiler | 🔴 高 | 约束引擎的核心，输出 Cypher MATCH 子句 |
| 图可视化 API（`graph.py`） | 🔴 高 | 变长路径 + `labels()` + 聚合统计 |
| Function 层查询（builtin/trace_business_impact） | 🟡 中 | 6-7 条 Cypher，涉及变长路径 |
| ImpactPropagator | 🟢 低 | 核心用语义化 API，只有 1 条 `labels()` 查询需改 |
| CRUD 写入（neo4j_store.py） | 🟡 中 | MERGE → upsert 改造 |

---

## 三、技术判定（6 个核心问题结论）

### 3.1 抽象层设计：⚠️ 可行——必须移除 query(cypher)

**设计：**
- 保留现有 6 个语义化方法（`merge_node`/`get_node`/`delete_node`/`merge_relation`/`delete_relation`/`get_relations`）
- 新增 5 个语义化查询方法，覆盖 84 处 Cypher 调用的高频模式：

| 新方法 | 覆盖场景 | 替代的 Cypher 模式 |
|---|---|---|
| `find_reachable(start_id, rel_types, direction, max_depth, return_fields)` | 路径遍历 | `MATCH (n)-[:REL*1..N]->(t) WHERE n.id=$id RETURN t.field` |
| `find_nodes(label, filters, contains, limit)` | 节点查找 | `MATCH (n:Label) WHERE n.name CONTAINS $name ... LIMIT N` |
| `count_by_label()` / `count_edges()` | 聚合统计 | `MATCH (n) RETURN labels(n)[0], count(*)` |
| `expand_neighbors(center, depth, limit, type_filter)` | 可视化邻居展开 | `MATCH path=(center)-[*1..N]-(neighbor)` |
| `execute_shape_query(query_ir)` | ShapeEvaluator 专用 | 由 PathCompiler 产生的 IR 对象 |

- **`query(cypher)` 从 `GraphStore` ABC 移除**，降级为 `Neo4jGraphStore` 独有方法。不是 deprecated，是移除。

### 3.2 PathExpression 作为全局 IR：❌ 不够用——分层 IR

**策略：**
- **PathExpression 不变**，继续服务 ShapeEvaluator（完美匹配场景）
- **新增 `TraversalQuery` IR**：描述路径遍历（起点+方向+关系类型列表(可通配)+深度+返回字段+过滤条件），覆盖 builtin / trace_business_impact / graph.py 路径查询
- 统计/聚合走专用语义化方法，不走 IR
- 不造万能 IR

### 3.3 编译器模式：✅ 选项 1（IR 对象）

```
PathCompiler.compile() → CompiledPath（结构化对象，不是字符串）
ShapeEvaluator._build_query() → ShapeQuery IR（path + where + return_field）
各后端编译器：
  Neo4jQueryCompiler.compile_shape_query(ShapeQuery) → Cypher 字符串
  NebulaQueryCompiler.compile_shape_query(ShapeQuery) → nGQL 字符串
```

ShapeEvaluator 只依赖 IR，不依赖任何后端。

### 3.4 NebulaGraph 强 Schema：⚠️ 混合策略

**内置 13 实体 26 关系：**
- 从 `schema.py` 的 `_LABEL_TO_DATACLASS` + `RELATION_TYPE_TO_NEO4J` 自动生成 DDL
- `NebulaSchemaInitializer` 在启动时执行 `CREATE TAG` / `CREATE EDGE`
- 每个 Tag 需定义属性类型（通过 dataclass 字段反射）

**用户自定义实体/关系：**
- 统一存入 `CustomEntity` Tag（含 id/name/entityType/properties JSON）+ `CUSTOM_REL` Edge
- 用 `entity_type` / `rel_type` 字段做软区分
- 保留"本体驱动"的动态扩展能力

**接口层：** 增加 `is_label_builtin(label) -> bool`，路由到不同的写入路径。

### 3.5 NebulaGraph 无 MERGE：⚠️ 应用层 upsert

**关键事实：** `INSERT VERTEX IF NOT EXISTS` 是只插入不更新。

**节点 upsert：**
1. OntoAgent 节点 id 是 UUID → 用作 NebulaGraph VID（天然唯一）
2. 写入前 `FETCH PROP ON {tag} {vid}` 检查存在性
3. 不存在 → `INSERT VERTEX`；存在 → `UPDATE VERTEX`
4. 两次网络往返，但 NebulaGraph 写入本身批量优化，单条延迟可接受

**边 upsert：**
1. 固定 rank = 0（保证 src→dst→edge_type 唯一）
2. `DELETE EDGE {type} {src}->{dst}@0` + `INSERT EDGE`（幂等）
3. 或接受查询层去重

**并发控制：**
- Butler 多 handler 写入需串行化或应用层锁
- 增量更新场景（单进程）无并发问题

### 3.6 性能风险：⚠️ 必须做 benchmark

- Neo4j 变长路径遍历延迟 < 10ms
- NebulaGraph 分布式查询（graphd→storaged 网络往返）预估 50-200ms
- ShapeEvaluator 一次操作可能触发 5 个 Shape → 如果 5×200ms=1s，实时性退化
- **Phase 4 必须包含性能基准测试**

---

## 四、分阶段实施计划

### Phase 0：POC 验证（1-2 天）
- 搭建 NebulaGraph 测试实例（Docker）
- 手写 nGQL 验证 5 个关键查询能否等效表达
- 产出性能基准数据（变长路径 `*1..3` 在 NebulaGraph 上的延迟）
- **Go/No-Go 决策点**

### Phase 1：抽象层重构（3-5 天）
- GraphStore ABC 新增 5 个语义化方法
- 移除 `query(cypher)` 到 Neo4jGraphStore
- 新增 `TraversalQuery` IR 数据结构
- PathCompiler 改为输出 `CompiledPath` 对象
- ShapeEvaluator 改为构建 `ShapeQuery` IR
- Neo4j 后端实现新接口（作为参照实现）
- **测试：Neo4j 后端全量回归测试通过**

### Phase 2：NebulaGraph 后端实现（4-6 天）
- `NebulaGraphStore(GraphStore)` 实现全部抽象方法
- `NebulaSchemaInitializer`：从 schema.py 自动生成 DDL
- `NebulaQueryCompiler`：消费 ShapeQuery / TraversalQuery IR → nGQL
- 实现 upsert 逻辑（FETCH + INSERT/UPDATE）
- `CustomEntity` Tag + `CUSTOM_REL` Edge 支持动态本体
- **测试：NebulaGraph 后端单元测试 + 集成测试**

### Phase 3：上层 Cypher 迁移（3-5 天）
- 22 个文件的 Cypher 逐步替换为语义化 API
- 优先级：ShapeEvaluator → 图可视化 API → Function 层 → 其他
- ImpactPropagator 只需改 1 条 `labels()` 查询
- **测试：双后端对比测试（相同数据集，相同查询结果）**

### Phase 4：集成验证 & 性能 benchmark（2-3 天）
- 端到端构建 + 查询在 NebulaGraph 上完整跑通
- 性能基准测试（L3 运行时评估：ShapeEvaluator 在真实 NebulaGraph 上执行）
- 配置切换：`ONTOAGENT_GRAPH_BACKEND=nebula`
- 文档更新

**总预估：13-21 天**

---

## 五、关键设计决策汇总

| 决策点 | 结论 | 理由 |
|---|---|---|
| query(cypher) 处理 | 从 ABC 移除 | deprecated 无强制力，必须物理隔离 |
| 统一 IR | 分层：PathExpression + TraversalQuery | 不造万能 IR |
| 编译器模式 | 选项 1（IR 对象） | 选项 2 会导致每个调用方分叉 |
| 强 Schema | 内置 DDL + 自定义通用 Tag | 兼顾类型安全和动态扩展 |
| MERGE 替代 | VID + FETCH + INSERT/UPDATE | NebulaGraph 无原生 upsert |
| Memgraph 评估 | N/A（强制 NebulaGraph） | 客户指定 |

---

## 六、新增文件清单

| 文件 | 职责 |
|---|---|
| `store/nebula_store.py` | NebulaGraphStore 实现 |
| `store/nebula_schema.py` | NebulaSchemaInitializer（DDL 自动生成）|
| `store/nebula_compiler.py` | NebulaQueryCompiler（IR → nGQL）|
| `store/ir.py` | TraversalQuery / CompiledPath / ShapeQuery IR 定义 |
| `store/factory.py` | GraphStore 工厂（按配置选择后端）|

---

## 七、修改文件清单

| 文件 | 改动 |
|---|---|
| `store/graph_store.py` | 新增 5 个抽象方法，移除 query(cypher) |
| `execution/path_compiler.py` | 输出 CompiledPath 对象而非 Cypher 字符串 |
| `execution/shape_evaluator.py` | 构建 ShapeQuery IR，调用 execute_shape_query |
| `store/neo4j_store.py` | 实现新接口，新增 Neo4jQueryCompiler |
| 22 个含 Cypher 的上层文件 | Cypher → 语义化 API 调用 |
| `config.py` | 新增 `GRAPH_BACKEND` 配置项 |
| `pyproject.toml` | 新增 `nebula-python` 依赖 |
