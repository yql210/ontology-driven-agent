# OntoAgent NebulaGraph 适配层全面反思报告

> **5 路独立审查综合**：3 个 Subagent（功能审计/测试审计/数据正确性）+ 2 个 Claude Code（架构审查/DevOps 审查）
> 日期：2026-07-26
> 状态：**终版反思，所有关键发现已实锤验证**
>
> **5 路审查耗时**：CC1(216s) + CC2(178s) + Agent1(289s/32次调用) + Agent2(180s) + Agent3(212s)
> **发现问题总计**：12 P0 + 14 P1 + 7 P2 = **33 个不兼容/缺陷**

---

## 一、最严重发现：当前适配层根本无法用于生产

### 🔴 P0-1：`NebulaGraphStore` 缺失 4 个核心接口 → 全量构建 100% 崩溃

**发现者**：Agent 3（数据正确性审计）
**验证状态**：✅ 实锤（`search_files` 确认 NebulaGraphStore 无实现，builder.py 有 17 处调用）

`NebulaGraphStore` **完全没有实现**：
| 缺失接口 | Neo4j 实现 | builder.py 调用位置 |
|---------|-----------|-------------------|
| `ensure_constraints()` | neo4j_store.py:542 | builder.py:296（**Stage 2 第一行**） |
| `merge_nodes_batch()` | neo4j_store.py:184 | builder.py:300,305,347,736,779,842,849,1091 |
| `merge_relations_batch()` | neo4j_store.py:238 | builder.py:325,372,762,804,872,1098 |
| `clear_all()` | neo4j_store.py:581 | builder.py:541 |

**后果**：`ontoagent build` 命令在 NebulaGraph 后端第一个调用 `ensure_constraints()` 就 `AttributeError` 崩溃。
**为什么 Phase 4 E2E "5/5 通过"**：E2E 脚本直接调 `merge_node()`（单条），完全绕过了 `builder.build()` 的批量路径。

### 🔴 P0-2：5 个生产入口绕过 factory 硬编码 Neo4j

**发现者**：CC 2（DevOps 审查）
**验证状态**：✅ 实锤（`search_files` 确认 7 处 `Neo4jGraphStore(` 直接实例化）

| 入口 | 行号 | 问题 |
|------|------|------|
| `api/cli.py` | 381 | `migrate` 命令永远连 Neo4j |
| `api/mcp_server.py` | 39 | MCP Server 启动连 Neo4j |
| `api/web/app.py` | 20 | Web API 连 Neo4j |
| `butler/handlers/base.py` | 30 | Butler 服务连 Neo4j |
| `pipeline/incremental_updater.py` | 155 | 增量更新连 Neo4j |

**后果**：即使设置 `GRAPH_BACKEND=nebula`，这 5 个入口仍连 Neo4j，且**不告警**。只有 `builder.py` 和 `agent/_helpers.py` 走了 factory。

### 🔴 P0-3：`NebulaSchemaInitializer` 从未被调用

**发现者**：CC 2（DevOps 审查）
**验证状态**：✅ 实锤（`search_files` 确认 src 里只有定义、零引用）

```
NebulaSchemaInitializer 引用：仅 nebula_schema.py:56（类定义本身）
```

**后果**：全新 NebulaGraph 实例冷启动后，Space/Tag/Edge/Index **永远不会被创建**。
所有 `INSERT VERTEX` 会因 `Tag not found` 失败 → 触发 `merge_node` fallback 到空属性占位 → 图里全是空节点。

### 🔴 P0-4：`$param` 参数化查询全部失败

**发现者**：CC 1（架构审查）
**验证状态**：✅ 实锤（实测 4/4 典型场景全部残留 `$param`）

上层代码有 **20+ 处**用 `$name`/`$entity_id`/`$fp`/`$limit` 参数化查询，但 `query()` 只做 `{key}` 模板替换：
```python
# nebula_store.py:371-372 — 只替换 {key}，不替换 $key
for key, value in params.items():
    final_stmt = final_stmt.replace("{" + key + "}", str(value))
```

**实测验证**：
| 查询场景 | 来源 | $param 残留 |
|---------|------|-----------|
| web router 变长路径 | graph.py:43 | ❌ `$name` `$limit` 残留 |
| tools.py 精确查找 | tools.py:114 | ❌ `$name` 残留 |
| impact_propagator | impact_propagator.py:247 | ❌ `$fp` 残留 |
| builtin 调用链 | builtin.py:90 | ❌ `$entity_id` 残留 |

**后果**：这些查询原样下发 NebulaGraph → 语法错误 → `RuntimeError`。上层多个 `except Exception: pass` 会吞掉异常返回空数据，**用户看到的是"没有结果"而不是"查询失败"**。

---

## 二、系统性问题：5 路审查的交叉发现

以下问题被**多路审查独立指出**，置信度最高：

### 2.1 关系属性完全丢失（Agent3 + CC2 + CC1 三路确认）

- `merge_relation` 的 `properties` 参数**接收后不写入**（INSERT 语句 `()` 为空）
- Edge type DDL 全是无属性 `CREATE EDGE ... ()`
- **丢失的业务数据**：`weight`（影响传播权重）、`confidence`（AST=1.0 vs LLM=0.7-0.95 的区分标记）、`provenance_source`/`extracted_at`
- `merge_relation` 返回 `properties or {}` **伪装写入成功**

### 2.2 fallback 静默数据丢失（Agent3 + CC2 + CC1 三路确认）

- `merge_node` 遇到 "Tag prop not found" → INSERT 空属性占位
- 节点"写成功了"但所有属性丢失
- 只打 `warning` 日志，**无计数器、无告警、无 fail-fast**
- **掩盖了 schema 与 entity_to_dict 的命名不一致**

### 2.3 get_node 丢失 Tag 信息（Agent3 独家发现）

- `get_node` 返回的 dict **不含 label/tag**
- 导致 `impact_propagator` 的 `node_label` 永远是 `"Unknown"`
- 导致 `incremental_updater` 的 concept/doc 重提取（按 `node_label == "ConceptEntity"` 过滤）**永远不触发**
- **增量更新的两个关键后续动作在 NebulaGraph 后端完全失效**

### 2.4 bool/int/float 全转字符串（Agent3 + CC1 确认）

- `_format_value` 把 `True`→`"True"`、`1.0`→`"1.0"`
- `_unwrap_value` 的 `as_string` 优先 → 读出永远是字符串
- `confidence` 做数值比较 → **Python 3 TypeError**
- `CapabilityEntity.enabled` 读出 `"False"` 字符串 → `if s.enabled` 永远为 True（非空字符串 truthy）

### 2.5 全 string schema + 强 schema 优势被放弃（CC1 + CC2 确认）

- 所有字段声明为 `string`，无法走数值索引
- `WHERE confidence > 0.8` 退化为字典序比较（`"99" > "100"` 为 true）
- 迁移到强类型需要重建 Space（ALTER TAG 不支持 string→int64）

### 2.6 SchemaVersion Tag 未创建（Agent 1 独家发现）

- `NebulaSchemaInitializer.create_tags()` 只遍历 `VALID_ENTITY_LABELS`，**不含 `SchemaVersion`**
- `schema_version.py` 要读写这个 Tag，但它不存在
- 即使修好 MERGE→UPSERT 问题，版本管理仍然失败（TagNotFound）

### 2.7 迁移脚本引用的 9 个 Edge type 未被 schema 创建（Agent 1 独家发现）

- 迁移脚本 `v1_2_0`、`v2_0_0` 引用 `CALLS_SERVICE`/`PUBLISHES_TO`/`CONSUMED_BY`/`PRODUCES`/`CONSUMES`/`COMPOSES_INTO`/`REALIZED_BY`/`PRECEDES`/`EQUIVALENT_TO`
- 这些**不在 `RELATION_TYPE_TO_NEO4J`** 中（domain/schema.py 只定义了 13 关系）
- `create_edges()` 只为 `RELATION_TYPE_TO_NEO4J.values()` 建 Edge type
- **即使迁移脚本语法修好，这些 Edge 写入也会失败**

### 2.8 aliases 字段存储为字符串 → set() 字符级拆分（Agent 1 独家发现）

- `_helpers.py:82` 对读出的 aliases 做 `set(aliases)`
- 但 aliases 在 NebulaGraph 里存为字符串（全 string schema）
- `set("auth")` → `{'a', 'u', 't', 'h'}` — **每个字符变成一个元素**
- 概念别名功能完全错乱

### 2.9 head(r) / length(path) / collect() 等 Cypher 函数不支持（Agent 1 独家发现）

| Cypher 函数 | 使用位置 | NebulaGraph 支持 |
|------------|---------|----------------|
| `head(r)` | mcp_server.py:186 | ❌ |
| `length(path)` | trace_business_impact.py:23 | ❌ |
| `collect(DISTINCT ...)` | aligner.py:305 | ⚠️ 支持，但语义可能不同 |
| `properties(r)` | mcp_server.py:205 | ❌ |
| `IS NOT NULL` | trace_business_impact.py:18 | ⚠️ 有限支持 |

---

## 三、架构层面的根本性问题（CC1 独家洞察）

### 3.1 ABC 接口设计错误是根因

```
GraphStore.query(cypher: str)  ← 这个签名是原罪
```

把查询语言绑定写进 ABC 契约 → 上层全写 Cypher → adapter 被迫做有损转换 → adapter 处理不了就静默失败。

**adapter 不是"该不该删"的问题，而是"它本不该存在"**——它的存在是 ABC 设计错误的症状。

### 3.2 schema 反射是伪自动化

`entity_field_names()` 反射 + `_EXTRA_FIELDS` 补丁 + `common_fields` 补丁 = **三层打补丁的伪自动化**。
已经有两个字段需要事后补丁（`codeParameters`、`provenanceSource`），未来每个新字段都会重蹈覆辙。

### 3.3 双后端是纯负债

- 代价：2x 实现 + 2x schema + 2x migration + ABC 表达力受限 + 假阳性测试覆盖
- 收益：仅"可切换"——但项目已强制生产 NebulaGraph
- **CC1 建议删除 Neo4j 实现**，让 ABC 自由贴合 NebulaGraph

---

## 四、测试体系的系统性失效（Agent2 独家洞察）

### 4.1 关键数字

| 指标 | 数值 |
|------|------|
| pytest 收集的测试总数 | 1702 |
| 真实 NebulaGraph 集成测试（pytest 内） | **0** |
| 实体类型覆盖率 | 1/13 = **7.7%** |
| 关系类型覆盖率 | 1/27 = **3.7%** |
| Phase 4 E2E 脚本 | 在 scripts/ 不在 pytest，**CI 不跑** |

### 4.2 mock 是"橡皮图章"

```python
# test_nebula_store.py:11-23
def _make_successful_result(*, rows=None):
    result = MagicMock()
    result.is_succeeded = MagicMock(return_value=True)  # ← 永远成功
```

mock 不模拟 NebulaGraph 的任何核心特性（强 schema、无 MERGE、保留字、变长路径语义）。**测试验证的是"代码调用了预期 API"，不是"NebulaGraph 真实返回正确数据"**。

### 4.3 "1644 通过"掩盖了什么

- migrations 坏了 → 测试全绿（mock 不执行真实 DDL）
- 变长路径可能返回错误数据 → 测试全绿（mock 返回预设值）
- `$param` 全部失败 → 测试全绿（mock 不检查语法）
- `merge_nodes_batch` 未实现 → 测试全绿（mock 了 GraphStore ABC）

---

## 五、修订后的优先级（综合 5 路意见）

| 优先级 | 问题 | 发现者 | 验证 | 工作量 |
|--------|------|--------|------|--------|
| **P0** | 实现 `merge_nodes_batch`/`merge_relations_batch`/`ensure_constraints`/`clear_all` | Agent3 | ✅实锤 | 大 |
| **P0** | 5 入口统一走 factory（消除硬编码 Neo4j） | CC2 | ✅实锤 | 小 |
| **P0** | NebulaSchemaInitializer 自动调用 | CC2 | ✅实锤 | 小 |
| **P0** | `$param` → `{key}` 全局替换或语义 API | CC1 | ✅实锤 | 中 |
| **P1** | 补真实 NebulaGraph 集成测试（13 实体 + 27 关系） | Agent2 | ✅确认 | 大 |
| **P1** | migrations 子系统改造（MERGE→UPSERT 等） | CC1+CC2 | ✅实锤 | 中 |
| **P1** | merge_relation 写入 properties（Edge 加字段） | Agent3+CC1+CC2 | ✅实锤 | 中 |
| **P1** | get_node 返回 label/tag | Agent3 | ✅确认 | 小 |
| **P1** | 删 fallback + schema 一致性单测 | Agent3+CC1 | ✅确认 | 小 |
| **P2** | bool/int/float 强类型或读出时转换 | Agent3+CC1 | ✅确认 | 中 |
| **P2** | 37 处硬编码 Cypher 收敛到语义 API | CC1 | ✅实锤 | 大 |
| **P2** | GraphStore ABC 重设计（删 query(cypher)） | CC1 | 架构建议 | 大 |
| **P3** | 强类型 schema（重建 Space） | CC1+CC2 | ✅确认 | 大 |
| **P3** | 删除 Neo4j 实现 | CC1 | 架构建议 | 中 |
| **P3** | metrics + healthz + 重试重连 | CC2 | ✅确认 | 中 |

---

## 六、最诚实的总结

**当前 NebulaGraph 适配层的真实状态是：单元测试通过率高，但生产环境完全不可用。**

5 路独立审查的交叉验证揭示了 4 个 P0 级阻断问题，其中最致命的是：
1. **全量构建 100% 崩溃**（4 个核心接口未实现）
2. **5 个入口绕过 factory**（设了 NebulaGraph 也连 Neo4j）
3. **schema 永远不会被创建**（SchemaInitializer 零调用）
4. **参数化查询全部失败**（`$param` 不处理）

这些问题之所以没被发现，是因为**测试体系是"橡皮图章"**——1697 个 mock 测试 + 0 个真实 NebulaGraph 测试。Phase 4 E2E 的 "5/5 通过"是一个精心绕过了所有失败路径的 mini repo 脚本，不能代表真实生产可用性。

**根本原因是架构层面的**：`GraphStore.query(cypher: str)` 这个 ABC 签名把"查询语言绑定"写进了契约，导致 adapter 成了有损转换器，成了持续打补丁的源头。

**修复路径**：先补 4 个缺失接口 + 修 5 个入口 + 自动 schema 初始化（让全量构建能跑），再补真实集成测试（建立 ground truth），再做架构层面的 ABC 重设计。
