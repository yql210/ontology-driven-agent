# NebulaGraph 适配层全量修复计划（v2 — CC 审核后修订）

> 基于 5 路独立审查（33 个问题）+ CC 计划审核（5 个技术修正 + 3 个调整建议）
> 日期：2026-07-26
> 状态：**终版，待用户确认**

---

## CC 审核的 5 个关键修正（v1 → v2）

| # | v1 错误 | v2 修正（CC 建议） |
|---|---------|-------------------|
| 1 | Phase 5 顺序：5.1→5.2→5.3→5.4 | **改为 5.3（batch 接口）最先做**——NebulaGraphStore 实例化即崩，没 batch 接口什么都验证不了 |
| 2 | DDL 等待用 `time.sleep(20)` | **改用探针 + tenacity 重试**（`SHOW TAGS` 探针，`stop_after_delay=120s, wait=2s`），与 Neo4j 对齐 |
| 3 | Phase 7 不拆（3-4 天） | **拆 7a/7b**——15 个"接近兼容"的查询 vs 3 个"需架构重构"的不应混估 |
| 4 | VID 类型风险放 Phase 9 | **提前到 Phase 5.0**——如果 VID 类型不对，batch INSERT 的转义都会错 |
| 5 | $param 方案 A 静默兜底 | **兜底时打 warning 日志** + **禁止 list/dict 走兜底**（强制上层改语义 API），Phase 7 验收时 grep 日志应为空 |

---

## Phase 5：基础设施修复（让系统能跑起来）

**目标**：`ontoagent build` 在 NebulaGraph 上完整跑通
**工作量**：3-4 天（CC 建议从 2-3 天上调）

### 5.0 前置检查（0.5 天）— CC 新增
- `SHOW SPACES` + `DESCRIBE SPACE` 确认 vid_type 一致性
- 如果不是 `FIXED_STRING(36)`，先迁移
- 核对迁移脚本引用的 9 个 Edge type 具体清单（输出对照表）

### 5.1 batch 接口实现（1.5-2 天）— CC 建议提前到第一位
**改动文件**：`nebula_store.py` + `graph_store.py`（ABC 声明）

| 方法 | NebulaGraph 实现 |
|------|-----------------|
| `ensure_constraints()` | VID 天然唯一，方法体确保 schema 已初始化 |
| `merge_nodes_batch(label, props_list)` | 批量 `INSERT VERTEX \`label\`(prop1,prop2) VALUES "v1":(...), "v2":(...)` |
| `merge_relations_batch(rel_data)` | 批量 `INSERT EDGE \`type\` VALUES "s1"->"t1":(...), "s2"->"t2":(...)` |
| `clear_all()` | `CLEAR SPACE`（CC 查证：保留 schema 只删数据） |

**注意**：CC 建议 5.1 和 Phase 6.1（merge_relation properties）合并做——batch INSERT EDGE 时就要决定属性 schema

### 5.2 入口统一 factory（0.5 天）
- `api/cli.py:381` → `create_graph_store(config)`
- `api/mcp_server.py:39` → `create_graph_store(config)`
- `api/web/app.py:20` → `create_graph_store(config)`
- `butler/handlers/base.py:30` → `create_graph_store(config)`
- `pipeline/incremental_updater.py:155` → `create_graph_store(config)`
- `pipeline/module_clustering.py:12` → 类型注解改 GraphStore

**验证**：grep 确认 `Neo4jGraphStore(` 只在 factory.py 和定义处

### 5.3 schema 自动初始化 + DDL 探针（1 天）— CC 修正：不用 sleep
- `NebulaGraphStore.__init__` 或首次 `_session_scope` 调用 `NebulaSchemaInitializer.initialize()`
- **DDL 等待用探针 + tenacity 重试**（不用 sleep）：
  ```python
  @retry(stop=stop_after_delay(120), wait=wait_fixed(2))
  def _wait_ddl_ready(session):
      result = session.execute("SHOW TAGS")
      if not result.is_succeeded() or result.is_empty():
          raise DDLNotReadyError()
  ```
- 补建 SchemaVersion Tag + 9 个迁移 Edge type

### 5.4 $param 兜底（0.5 天）— CC 修正：加 warning + 禁 list/dict
- `NebulaGraphStore.query()` 增加 `$key` → 值替换
- **兜底时打 warning 日志**（`logger.warning("$param fallback used for query: %s", key)`）
- **list/dict 类型走兜底直接抛异常**（强制上层改语义 API，避免 Python repr 问题）
- 标记所有 $param 使用点为 TODO，Phase 7 验收时 grep 日志应为空

### Phase 5 验收标准
- [ ] `GRAPH_BACKEND=nebula ontoagent build ./mini_repo` 完整跑通
- [ ] SHOW TAGS 显示全部 Tag（含 SchemaVersion）
- [ ] SHOW EDGES 显示全部 Edge type（含迁移引用的 9 个）
- [ ] grep 确认无 `Neo4jGraphStore(` 直接实例化
- [ ] grep 确认无 `time.sleep` 硬等待
- [ ] $param 查询执行成功（4 个典型场景）

---

## Phase 6：数据正确性修复

**目标**：写入 = 读出，无静默丢失
**工作量**：2-2.5 天

### 6.1 关系属性支持（1 天）— CC 建议与 5.1 合并
- Edge type 加通用字段：`weight double, provenanceSource string, confidence double, extractedAt string`
- `merge_relation` 用 `UPSERT EDGE`（CC 查证 3.6+ 支持，语法见下）
  ```ngql
  UPSERT EDGE ON `CALLS` "src"->"dst"@0 SET weight=1.0, confidence=0.9;
  ```
- rank=0 保持语义（避免同源同目标留多条）

### 6.2 get_node 返回 label（0.5 天）
- `FETCH PROP ON * vid YIELD id(vertex), tags(vertex) AS tags, properties(vertex) AS props`
- 返回 dict 加 `"label": tags[0] if tags else None`
- 修复 impact_propagator 的 node_label + incremental_updater 的 concept/doc 重提取

### 6.3 删 fallback + schema 一致性（0.5 天）
- 删除 merge_node fallback 降级（nebula_store.py:181-192）
- `entity_to_dict` 加断言：`assert set(d.keys()) ⊆ schema_fields(label)`
- 新建 `ENTITY_EXTRA_FIELDS` 封闭登记表

### 6.4 序列化字段处理（0.5 天）
- list/set 字段存为 JSON 字符串
- 读出时 `json.loads` 还原
- 修复 aliases 的 `set()` 字符级拆分

### Phase 6 验收标准
- [ ] **fuzz 写入测试**（CC 建议）：随机生成 100 个 entity + 50 个 relation，读回逐字段对比
- [ ] get_node 返回 label 字段
- [ ] schema 不匹配时抛异常
- [ ] aliases 读回是 set 不是字符拆分

---

## Phase 7a：查询层适配（已接近兼容的部分）

**目标**：adapter 能自动转换的查询，补齐小改
**工作量**：1-1.5 天（CC 建议从 Phase 7 拆出）

### CC 发现：21 个查询改造点实际只有 ~6 个需要大改
| 类型 | 数量 | 处理方式 |
|------|------|---------|
| ✅ adapter 自动转（接近兼容） | ~15 | Phase 7a 验收时跑一次确认 |
| ⚠️ 需小改 | ~3 | $types=[]、NOT (n)--()、head(r) |
| 🔴 需架构重构 | ~3 | Phase 7b |

### 7a.1 属性命名统一（$types 除外的小改）
- `file_path` → `filePath`、`entity_type` → `entityType`（camelCase 统一）

### 7a.2 函数替代
- `head(r)` → 改用 `get_relations()` 返回第一条
- `NOT (n)--()` → nGQL `LOOKUP` + Python filter 或 `GO FROM vid OVER *`

### 7a.3 export_graph 验证（CC 建议降级为 P1）
- adapter 已覆盖 labels/startNode/properties 转换
- 验收时跑一次确认，不单独改造

### Phase 7a 验收标准
- [ ] $param 兜底 warning 日志 grep 为空（所有 $param 已改为语义 API 或原生 nGQL）
- [ ] 15 个"接近兼容"查询在真实 NebulaGraph 执行成功

---

## Phase 7b：DDL / 版本管理 / 反向 BFS 重构

**目标**：需架构改造的 3 个大块
**工作量**：2-3 天（CC 建议从 Phase 7 拆出）

### 7b.1 migrations 子系统全面改造
- `CREATE CONSTRAINT ... REQUIRE` → NebulaGraph 原生（VID 天然唯一，空操作 + 索引）
- `schema_version.py` 的 `MERGE ... SET` → `UPSERT VERTEX ON SchemaVersion`
- `runner.py:128` 的 `DETACH DELETE` → `DELETE VERTEX vid WITH EDGE`
- `$param` → 已在 Phase 5 解决

### 7b.2 变长路径 + 反向 BFS 重构
| 查询 | 改造方案 |
|------|---------|
| `mcp_server.impact_analysis` | 改用 `get_relations()` + Python BFS |
| `web/router/graph.py` 中心展开 | nGQL `GO ... OVER` + Python 组装 或 BFS |
| `builtin.py` trace_call_chain | 改用 BFS（已有 impact_propagator 模式） |
| `trace_business_impact.py` | 反向 BFS（`length(path)` 用深度计数替代） |

### 7b.3 其他查询修复
- `aligner._graph_structure_match`：`collect(DISTINCT)` → Python 聚合
- `incremental_updater._validate_graph_integrity`：`WHERE NOT (n)--()` 改 nGQL

### Phase 7b 验收标准
- [ ] `ontoagent migrate` 在 NebulaGraph 跑通
- [ ] Web API `/graph` 可视化返回正确数据
- [ ] MCP `impact_analysis` 返回正确调用链
- [ ] 跑完整 `pytest tests/integration/`，通过率 ≥ 90%

---

## Phase 8：测试体系重建

**目标**：真实 NebulaGraph ground truth
**工作量**：2-3 天

### 8.1 真实 NebulaGraph 集成测试
- `tests/integration/test_nebula_e2e_build.py` — 全量 build 13 实体 + 27 关系
- `tests/integration/test_nebula_queries.py` — 4 类查询模式
- 纳入 pytest，加 `@pytest.mark.integration` + skip 条件

### 8.2 mock 增强
- mock 模拟 NebulaGraph 强 schema（未知字段报错）
- mock 模拟 $param 不支持

### 8.3 E2E 全链路
- express_intent → ActionExecutor → 真实 store
- build → update → query 生命周期

### Phase 8 验收标准
- [ ] pytest 集成测试 ≥ 20 个，连真实 NebulaGraph
- [ ] 实体覆盖率 ≥ 80%、关系覆盖率 ≥ 70%

---

## Phase 9：生产可靠性

**目标**：7x24 稳定运行 + 可观测性
**工作量**：2-3 天

### 9.1 重试重连
- `_session_scope` 包 tenacity（与 Neo4j 一致）
- ConnectionPool 健康检查

### 9.2 性能优化
- Adapter 单例化
- session 复用（批量写入用同一 session）

### 9.3 索引 + 可观测性
- `filePath`/`entityType` 索引
- Prometheus metrics（latency/error/fallback/pool_used）
- `/healthz` 端点

---

## Phase 10（长期）：架构优化

**工作量**：1-2 周

### 10.1 GraphStore ABC 重设计
- 新增语义 API：`find_nodes`/`find_neighbors`/`traverse`/`count_entities`
- `query(cypher)` → `raw_query()`，仅供 LLM 通道

### 10.2 删除 adapter / 评估删除 Neo4j 后端

### 10.3 强类型 schema（新 Space）

---

## 修订后的执行顺序与依赖

```
Phase 5.0（前置检查 VID + Edge type 对照表）         0.5 天
    ↓
Phase 5.1（batch 接口 + Edge 属性 schema）  ← 最先做  1.5-2 天
    ↓
Phase 5.2（入口统一 factory）                        0.5 天
    ↓
Phase 5.3（schema init + DDL 探针）                  1 天
    ↓
Phase 5.4（$param 兜底 + warning）                   0.5 天
    ↓ （Phase 5 合计 3-4 天）
Phase 6（数据正确性）                                2-2.5 天
    ↓
Phase 7a（查询层适配 — 接近兼容部分）                 1-1.5 天
    ↓
Phase 7b（DDL/版本管理/反向 BFS 重构）               2-3 天
    ↓
Phase 8（测试体系）                                  2-3 天
    ↓
Phase 9（生产可靠性）                                2-3 天
    ↓
Phase 10（架构优化，长期）                           1-2 周
```

**关键路径**：Phase 5 → 6 → 7a → 7b → 8 ≈ **11-14 天**

### CC 建议的可行性 spike
先跑 Phase 5.1（0.5 天 batch 接口）+ 5.2（0.5 天 factory）作为"可行性 spike"——如果顺利，后面的工作量估算才有意义。

---

## 每个 Phase 完成后的统一验收门（CC 建议）

1. `butler/engine.py` 的 EventBus 在 NebulaGraph 上 smoke test 一次
2. Phase 5 完成后：grep 无 `time.sleep` 硬等待
3. Phase 6 完成后：fuzz 写入测试（100 entity + 50 relation 读回对比）
4. Phase 7 完成后：跑完整 `pytest tests/integration/`，通过率 ≥ 90%
