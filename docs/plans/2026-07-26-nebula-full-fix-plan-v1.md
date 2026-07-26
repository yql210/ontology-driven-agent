# NebulaGraph 适配层全量修复计划（草案 v1 — 待 CC 审核）

> 基于 5 路独立审查发现的 33 个问题（12 P0 + 14 P1 + 7 P2）
> 日期：2026-07-26
> 状态：**草案，待 CC 审核后交用户确认**

---

## 修复原则

1. **先让系统能跑，再让数据正确，最后让架构干净**——分阶段交付，每阶段可独立验证
2. **每个 Phase 结束必须有真实 NebulaGraph E2E 验证**——不再用 mock 自欺欺人
3. **生产代码用 CC CLI 写**（`cat plan.md | claude -p --dangerously-skip-permissions`）
4. **先出方案 → CC 评审 → 用户确认 → 再执行**

---

## 问题全量清单 → Phase 映射

### P0 生产阻断（12 个）

| # | 问题 | 归属 Phase | 依赖 |
|---|------|-----------|------|
| P0-1 | NebulaGraphStore 缺 merge_nodes_batch/merge_relations_batch/ensure_constraints/clear_all | Phase 5 | 无 |
| P0-2 | 5 入口绕过 factory 硬编码 Neo4j | Phase 5 | 无 |
| P0-3 | NebulaSchemaInitializer 从未被调用 | Phase 5 | 无 |
| P0-4 | $param 参数化查询全部失败 | Phase 5 | 无 |
| P0-5 | migrations 用 CREATE CONSTRAINT...REQUIRE | Phase 7 | Phase 5 |
| P0-6 | schema_version 用 MERGE...SET | Phase 7 | Phase 5 |
| P0-7 | schema_version.get_current_db_version() 查询不兼容 | Phase 7 | Phase 5 |
| P0-8 | runner.py rollback 用 DETACH DELETE | Phase 7 | Phase 5 |
| P0-9 | mcp_server impact_analysis 变长路径+head(r)+$param | Phase 7 | Phase 5 |
| P0-10 | mcp_server export_graph labels(n)+startNode+properties(r) | Phase 7 | Phase 5 |
| P0-11 | Web API /graph 变长路径+LIMIT $param | Phase 7 | Phase 5 |
| P0-12 | Web API a.id IN $ids 列表参数化 | Phase 7 | Phase 5 |
| P0-13 | builtin.py 变长路径+$param | Phase 7 | Phase 5 |
| P0-14 | trace_business_impact 反向变长路径+length(path) | Phase 7 | Phase 5 |

### P1 功能缺失/错误（14 个）

| # | 问题 | 归属 Phase | 依赖 |
|---|------|-----------|------|
| P1-1 | SchemaVersion Tag 未创建 | Phase 6 | Phase 5 |
| P1-2 | 9 个 Edge type 未被 schema 创建 | Phase 6 | Phase 5 |
| P1-3 | merge_relation 忽略 properties | Phase 6 | Phase 5 |
| P1-4 | merge_relation DELETE+INSERT 非原子 | Phase 6 | Phase 5 |
| P1-5 | agent/tools.py 5处 $param | Phase 7 | Phase 5 |
| P1-6 | action_executor 属性名混乱+匿名节点 | Phase 7 | Phase 5 |
| P1-7 | module_clustering file_path snake_case | Phase 7 | Phase 5 |
| P1-8 | impact_propagator map_files_to_nodes | Phase 7 | Phase 5 |
| P1-9 | incremental_updater 两处 inline+$param | Phase 7 | Phase 5 |
| P1-10 | incremental_updater WHERE NOT (n)--() | Phase 7 | Phase 5 |
| P1-11 | cleanup_orphan_nodes 语义不一致 | Phase 7 | Phase 5 |
| P1-12 | aligner 双重不兼容 | Phase 7 | Phase 5 |
| P1-13 | get_node 多 Tag / 丢 Tag 信息 | Phase 6 | Phase 5 |
| P1-14 | _helpers aliases 字符级拆分 | Phase 6 | Phase 5 |

### P2 性能/边界（7 个）

| # | 问题 | 归属 Phase |
|---|------|-----------|
| P2-1 | Adapter 每次 new | Phase 9 |
| P2-2 | 每次操作 USE SPACE 开销 | Phase 9 |
| P2-3 | DDL 异步生效未处理 | Phase 5 |
| P2-4 | VID FIXED_STRING(36) 截断风险 | Phase 9 |
| P2-5 | _format_value 全转字符串 | Phase 9/10 |
| P2-6 | adapter regex 误伤 | Phase 9 |
| P2-7 | validate_graph_query 拦截 | Phase 9 |

---

## Phase 5：基础设施修复（让系统能跑起来）

**目标**：让 `ontoagent build` 在 NebulaGraph 上能完整跑通（不崩、能写入、schema 自动建）
**预计工作量**：2-3 天

### 5.1 统一 factory 入口（P0-2）
**改动文件**：5 个入口
- `api/cli.py:381` → `create_graph_store(config)`
- `api/mcp_server.py:39` → `create_graph_store(config)`
- `api/web/app.py:20` → `create_graph_store(config)`
- `butler/handlers/base.py:30` → `create_graph_store(config)`
- `pipeline/incremental_updater.py:155` → `create_graph_store(config)`
- `pipeline/module_clustering.py:12` → 类型注解改 GraphStore

**验证**：grep 确认 src 内 `Neo4jGraphStore(` 只出现在 factory.py 和 neo4j_store.py 定义处

### 5.2 NebulaSchemaInitializer 自动调用 + DDL 等待（P0-3, P2-3）
**改动**：
- `NebulaGraphStore.__init__` 末尾或首次 `_session_scope` 时调用 `NebulaSchemaInitializer.initialize()`
- 加 `time.sleep(20)` 等待 DDL 生效（带可配置超时）
- `initialize()` 内部加重试（DDL 异步生效，首次可能失败）

**验证**：全新 NebulaGraph Space，build 后 SHOW TAGS / SHOW EDGES 返回完整列表

### 5.3 实现 4 个 batch 接口（P0-1）
**改动文件**：`nebula_store.py` + `graph_store.py`（ABC 声明）

| 方法 | NebulaGraph 实现方式 |
|------|---------------------|
| `ensure_constraints()` | VID 天然唯一，方法体为空（或调 schema init 确保 Tag 存在） |
| `merge_nodes_batch(label, props_list)` | 批量 `INSERT VERTEX label(prop1,prop2) VALUES "v1":(...), "v2":(...)` |
| `merge_relations_batch(rel_data)` | 批量 `INSERT EDGE type VALUES "s1"->"t1":(), "s2"->"t2":()` |
| `clear_all()` | `CLEAR SPACE` 或逐 Tag `DELETE VERTEX` + 重建索引 |

**验证**：真实 NebulaGraph 跑 `ontoagent build ./mini_repo`，节点和关系全部写入

### 5.4 $param 全局支持（P0-4）
**两种方案（需 CC 评审选哪个）**：

**方案 A（query 层兜底，影响小）**：`NebulaGraphStore.query()` 增加 `$key` → 值替换
```python
if params:
    for key, value in params.items():
        # 同时替换 {key} 和 $key
        final_stmt = final_stmt.replace("{" + key + "}", str(value))
        final_stmt = final_stmt.replace("$" + key, _format_value(value).strip('"'))
```
- 优点：不动上层 20+ 处代码
- 缺点：字符串替换有注入风险；list 参数 `$ids` 需特殊处理

**方案 B（上层改造，彻底）**：20+ 处 `$key` 改为 `{key}` 或语义 API
- 优点：根治
- 缺点：工作量大，且 LLM 生成的查询仍可能带 `$param`

**建议**：先用方案 A 兜底（Phase 5），长期走方案 B（Phase 10）

**验证**：4 个典型 $param 查询场景在真实 NebulaGraph 执行成功

### Phase 5 验收标准
- [ ] `GRAPH_BACKEND=nebula ontoagent build ./mini_repo` 完整跑通
- [ ] SHOW TAGS 显示 13 个 Tag（+SchemaVersion）
- [ ] SHOW EDGES 显示所有 Edge type（含迁移脚本引用的 9 个）
- [ ] 4 个 $param 查询场景执行成功
- [ ] grep 确认无 `Neo4jGraphStore(` 直接实例化（除 factory/定义）

---

## Phase 6：数据正确性修复

**目标**：写入的数据 = 读出的数据，无静默丢失
**预计工作量**：2 天

### 6.1 merge_relation 写入 properties（P1-3, P1-4）
- `nebula_schema.py::create_edges` 给每个 Edge type 加通用字段：`weight double, provenanceSource string, confidence double, extractedAt string`
- `merge_relation` 的 INSERT 语句写入 properties
- DELETE+INSERT 改为 UPSERT EDGE（如果 NebulaGraph 支持）或加错误恢复

### 6.2 get_node 返回 label/tag（P1-13）
- `FETCH PROP ON * vid YIELD id(vertex), tags(vertex), properties(vertex)`
- 返回 dict 加 `"label": tags[0]`
- 修复 impact_propagator 的 node_label 和 incremental_updater 的 concept/doc 重提取

### 6.3 删 fallback + schema 一致性单测（跨审查共识）
- 删除 merge_node 的 fallback 降级（181-192 行）
- `entity_to_dict` 加断言：`assert set(d.keys()) ⊆ schema_fields(label)`
- 新建 `ENTITY_EXTRA_FIELDS` 登记表，封闭管理动态字段

### 6.4 补建 SchemaVersion Tag + 9 个 Edge type（P1-1, P1-2）
- `NebulaSchemaInitializer.create_tags()` 加 SchemaVersion
- `RELATION_TYPE_TO_NEO4J` 或独立列表补 9 个迁移 Edge type

### 6.5 序列化字段处理（P1-14）
- list/set 字段存为 JSON 字符串
- 读出时 `json.loads` 还原
- 修复 aliases 的 `set()` 字符级拆分

### Phase 6 验收标准
- [ ] 写入 CodeEntity+CALLS（带 weight/confidence），读回数据完整且类型正确
- [ ] get_node 返回 label 字段
- [ ] schema 不匹配时抛异常而非静默降级
- [ ] aliases 读回是 set 不是字符拆分

---

## Phase 7：查询兼容性修复（12 个 P0 + 9 个 P1）

**目标**：所有上层查询在 NebulaGraph 上返回正确结果
**预计工作量**：3-4 天

### 7.1 migrations 子系统全面改造（P0-5, P0-6, P0-7, P0-8）
- `CREATE CONSTRAINT ... REQUIRE` → NebulaGraph 原生（TAG INDEX 或空操作，VID 已唯一）
- `MERGE ... SET` → `UPSERT VERTEX ON SchemaVersion`
- `DETACH DELETE` → `DELETE VERTEX vid WITH EDGE`
- `$param` → 已在 Phase 5 解决

### 7.2 变长路径查询改造（P0-9,11,12,13,14 + P1-8,12）
**两种方案**：
- **方案 A**：改写为原生 nGQL（NebulaGraph 支持 `*1..N` 但语法有差异）
- **方案 B**：改用 `get_relations()` + Python BFS（impact_propagator 已是此模式）

逐个改造：
- `mcp_server.impact_analysis` → 方案 B
- `mcp_server.export_graph` → 原生 nGQL（`FETCH PROP` 全量）
- `web/router/graph.py` 中心展开 → 方案 B 或 nGQL `GO ... OVER`
- `builtin.py` trace_call_chain → 方案 B
- `trace_business_impact.py` → 方案 B（`length(path)` 用 BFS 深度替代）

### 7.3 属性命名统一（P1-6, P1-7, P1-9）
- 全部改为 camelCase（与 schema 一致）
- `file_path` → `filePath`，`entity_type` → `entityType`
- 或在 adapter 加 snake_case → camelCase 自动转换

### 7.4 Cypher 函数替代（P0-9 head(r), P0-14 length(path), P1-12 collect）
- `head(r)` → 改用 `get_relations()` 返回的第一条
- `length(path)` → BFS 深度计数
- `collect(DISTINCT ...)` → Python 端聚合
- `properties(r)` → `get_relations()` 已返回 properties

### 7.5 其他查询修复（P1-5, P1-10, P1-11）
- `agent/tools.py` 5 处 $param → Phase 5 已兜底，长期改语义 API
- `WHERE NOT (n)--()` → nGQL 等价写法
- `cleanup_orphan_nodes` 语义对齐

### Phase 7 验收标准
- [ ] `ontoagent migrate` 在 NebulaGraph 跑通
- [ ] Web API `/graph` 可视化返回正确数据
- [ ] MCP `impact_analysis` 返回正确调用链
- [ ] `trace_call_chain` / `trace_business_impact` Action 可用

---

## Phase 8：测试体系重建

**目标**：建立真实 NebulaGraph ground truth，不再用 mock 自欺
**预计工作量**：2-3 天

### 8.1 真实 NebulaGraph 集成测试
- `tests/integration/test_nebula_e2e_build.py` — 全量 build 13 实体 + 27 关系
- `tests/integration/test_nebula_queries.py` — 4 类查询模式（单跳/变长/属性/聚合）
- 加 `@pytest.mark.integration` + 连接 skip 条件
- 纳入 pytest（改 testpaths 或 conftest）

### 8.2 mock 增强
- mock 模拟 NebulaGraph 强 schema（未知字段报错）
- mock 模拟 $param 不支持
- mock 模拟保留字冲突

### 8.3 E2E 全链路测试
- express_intent → ActionExecutor → 真实 NebulaGraph store
- build → update → query 完整生命周期

### Phase 8 验收标准
- [ ] pytest 集成测试 ≥ 20 个，全部连真实 NebulaGraph
- [ ] 实体覆盖率 ≥ 80%（至少 10/13）
- [ ] 关系覆盖率 ≥ 70%（至少 19/27）

---

## Phase 9：生产可靠性（P2 + DevOps）

**目标**：7x24 稳定运行，有可观测性
**预计工作量**：2-3 天

### 9.1 重试重连（CC2 P0-3）
- `_session_scope` 包 tenacity（与 Neo4j 一致）
- ConnectionPool 健康检查

### 9.2 性能优化（P2-1, P2-2）
- Adapter 单例化
- session 复用（批量写入用同一个 session）
- 批量 INSERT VERTEX/EDGE

### 9.3 索引补建（CC2 建议）
- `filePath`、`entityType` 索引
- Edge 属性索引

### 9.4 可观测性（CC2 P1）
- Prometheus metrics（latency/error/fallback/pool_used）
- `/healthz` 端点
- 日志加 request_id/trace_id

---

## Phase 10（长期）：架构优化

**目标**：消灭 adapter，ABC 重设计
**预计工作量**：1-2 周

### 10.1 GraphStore ABC 重设计（CC1 建议）
- 新增语义 API：`find_nodes` / `find_neighbors` / `traverse` / `count_entities`
- `query(cypher)` 降级为 `raw_query()`，仅供 LLM 通道

### 10.2 删除 adapter（或降级为 LLM 专用）

### 10.3 强类型 schema（新 Space）

### 10.4 评估删除 Neo4j 后端

---

## 执行顺序与依赖图

```
Phase 5（基础设施）── 必须先完成
    ├── Phase 6（数据正确性）── 依赖 5 的 schema init
    ├── Phase 7（查询兼容）── 依赖 5 的 $param 支持
    │       └── Phase 8（测试体系）── 依赖 7 的查询可用
    │               └── Phase 9（可靠性）── 依赖 8 的 ground truth
    └── Phase 10（架构）── 长期，依赖 8 验证不退化
```

**关键路径**：Phase 5 → 6 → 7 → 8（约 9-12 天）
Phase 9 和 10 可并行或后续迭代。
