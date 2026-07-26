# OntoAgent 图数据库适配方案 C：CQRS 内存图方案

> **状态：待 Claude Code 技术反思**
> **日期：2026-07-26**
> **决策路径：生产环境强制 NebulaGraph，V2 nGQL 全适配方案工作量过大（13-21 天），寻求更优解**

---

## 一、核心洞察

V2 方案一直在解决"怎么让 NebulaGraph 听懂 OntoAgent 的查询"。但真正的问题是"OntoAgent 怎么在生产环境跑起来"——这两个问题的解法完全不同。

### 关键发现

OntoAgent 的所有图遍历都是**有限深度（1-3 跳）**的，不是无限深度的复杂图分析：

| 模块 | 遍历深度 | 实现方式 |
|---|---|---|
| ShapeEvaluator | `*1..3` | Cypher 变长路径 |
| ImpactPropagator 核心 BFS | 逐跳，有 max_depth | **已用语义化 `get_relations()` API，不用图数据库原生遍历** |
| 图可视化 | `[*1..{depth}]`（depth≤3） | Cypher 变长路径 |
| 业务追溯 | `CALLS*1..10` | Cypher 变长路径（但上限固定） |

ImpactPropagator 已经证明：**有限深度遍历不需要图数据库的原生查询优化，在内存里做反而更快。**

---

## 二、方案 C：CQRS 分离架构

### 核心思路

```
写入 → NebulaGraph（持久化，source of truth）
查询 → 内存图（计算引擎，μs 级延迟）
```

### 架构图

```
┌─────────────────────────────────────────────────┐
│                OntoAgent 业务层                    │
│    (ShapeEvaluator / ImpactPropagator / API)     │
└──────────────┬──────────────────┬────────────────┘
               │ 读               │ 写
               ▼                  ▼
     ┌─────────────────┐  ┌──────────────────┐
     │  内存图查询引擎   │  │ NebulaGraph 持久化 │
     │  (networkx)      │  │ (原生 nGQL INSERT) │
     │  μs 级延迟        │  │  source of truth   │
     └────────┬────────┘  └──────────────────┘
              │ 启动时加载
              │ 增量同步
              ▼
         NebulaGraph
```

### 数据流

1. **启动时**：从 NebulaGraph 全量加载到内存图
2. **写入时**：先写 NebulaGraph（持久化），成功后更新内存图
3. **查询时**：直接走内存图 API，不碰 NebulaGraph 查询引擎
4. **增量更新**：仅同步变更部分

---

## 三、为什么这比 V2 nGQL 全适配方案好

| 维度 | V2（nGQL 适配） | C（CQRS 内存图） |
|---|---|---|
| 工作总量 | 13-21 天 | 5-8 天 |
| 119 条 Cypher | 全部重写为语义化 API + nGQL 编译器 | **不动**——查询走内存图 |
| query(cypher) 问题 | 移除 + 替换全部调用方 | **消失**——内存图直接遍历 |
| PathExpression IR | 不够用，需新增 TraversalQuery | **不需要**——内存图直接用 PathExpression 语义遍历 |
| NebulaGraph 强 Schema | 混合策略（内置 DDL + 通用 Tag） | **最简 Tag**（id + properties JSON），NebulaGraph 只管 INSERT |
| NebulaGraph 无 MERGE | FETCH + UPDATE 两步 | **消失**——内存图做 upsert 原子操作 |
| 查询性能 | 50-200ms（网络往返） | **μs 级**（内存遍历） |
| ShapeEvaluator 改动 | 大手术（PathCompiler → IR → nGQL） | **几乎不动**——只换底层 GraphStore 实现 |
| nGQL 编译器 | 必须开发 | **不需要** |

---

## 四、内存占用评估

OntoAgent 解析的是代码仓库 AST：
- 中型仓库：~10 万节点 + ~100 万边
- 节点平均 500 字节属性 + 边平均 100 字节属性
- 总内存约 150MB

即使大型仓库（百万节点），也在 1-2GB 范围内。现代服务器毫无压力。

---

## 五、实施计划

### Phase 1：内存图 GraphStore 实现（2-3 天）

新增 `InMemoryGraphStore(GraphStore)`：
- 基于 `networkx.MultiDiGraph`
- 实现全部 7 个抽象方法 + 现有 `query(cypher)` 后门
- `query(cypher)` 内部：用轻量 Cypher 解析器做有限模式匹配（或直接用 `match` 库），覆盖 OntoAgent 实际使用的 Cypher 子集
- **Neo4j 后端全量回归测试通过**（内存图作为 Neo4j 的等效替代）

### Phase 2：NebulaGraph 写入层（2-3 天）

新增 `NebulaGraphWriter`：
- 只负责写入（INSERT），不负责查询
- 最简 Tag 设计：每个实体类型一个 Tag，属性用 dataclass 反射生成
- 写入策略：`INSERT VERTEX IF NOT EXISTS`（节点）+ rank=0 固定（边）
- 不需要 MERGE——内存图已保证幂等

### Phase 3：启动加载 + 增量同步（1-2 天）

- 启动时从 NebulaGraph 全量加载到内存图
- 写入时双写（NebulaGraph → 内存图）
- 增量更新仅同步变更部分

### Phase 4：集成测试（1 天）

- 端到端构建 + 查询在 NebulaGraph + 内存图上完整跑通
- ShapeEvaluator L3 运行时评估在真实数据上执行
- 性能 benchmark

**总预估：6-9 天**

---

## 六、风险与应对

| 风险 | 严重度 | 应对 |
|---|---|---|
| 启动时全量加载耗时 | 🟡 中 | 大图谱冷启动几秒~几十秒。可优化为惰性加载（按需加载子图） |
| 多实例数据一致性 | 🟡 中 | 单写入者模式，或写入时通知其他实例刷新缓存 |
| 进程崩溃丢内存 | 🟢 低 | NebulaGraph 是 source of truth，重启后重新加载 |
| query(cypher) 内存实现 | 🟡 中 | 需覆盖的 Cypher 子集有限（MATCH/MERGE/WHERE/RETURN/labels），不实现完整 Cypher |
| networkx 性能瓶颈 | 🟢 低 | 百万级节点 BFS 延迟 < 10ms，远超网络数据库 |

---

## 七、需要 Claude Code 反思的核心问题

1. **内存图方案是否可行？** OntoAgent 的查询模式（有限深度遍历 + CRUD）是否适合全部走内存图？有没有必须依赖图数据库原生引擎的场景？

2. **query(cypher) 在内存图上怎么处理？** 当前 119 条 Cypher 有 84 处走 `store.query(cypher)`。在内存图方案中：
   - 选项 A：写一个轻量 Cypher 子集解析器，在 networkx 上执行
   - 选项 B：把这些 Cypher 全部替换为语义化方法调用
   - 选项 C：用 Neo4j embedded（Java 模式，无网络开销）作为内存图引擎
   - 哪个最优？

3. **networkx 是否是正确的内存图引擎？** 有没有更合适的选择（igraph / graph-tool / rapids cuGraph）？

4. **双写一致性如何保证？** NebulaGraph 写入成功但内存图更新失败时，数据不一致怎么处理？

5. **多实例部署场景如何处理？** 如果 OntoAgent 部署多个副本，每个副本的内存图怎么同步？是否需要引入消息队列？

6. **是否有更好的变种方案？**
   - 是否应该用 RedisGraph（内存图数据库）替代 networkx + NebulaGraph 的组合？
   - 是否应该用 SQLite + AdjacencyList 替代 NebulaGraph？
   - NebulaGraph 作为持久化层，是否可以换成一个更简单的存储（如 PostgreSQL JSONB）？

---

## 八、与方案 A（加 Neo4j）的对比

| 维度 | A（加 Neo4j） | C（CQRS 内存图） |
|---|---|---|
| 工作量 | ~0 | 5-8 天 |
| 额外组件 | 需要部署 Neo4j | 仅 NebulaGraph（已有） |
| 架构复杂度 | 不变 | 增加内存图层 |
| 性能 | 优秀（Neo4j 原生） | 更优（内存遍历） |
| 客户接受度 | 取决于运维政策 | 不引入新组件 |

**当前决策：客户强制 NebulaGraph，方案 A 需确认运维是否允许额外 Neo4j。方案 C 不引入新组件。**
