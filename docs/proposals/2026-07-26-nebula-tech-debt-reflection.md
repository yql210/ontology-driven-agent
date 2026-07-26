# NebulaGraph 适配层技术债反思与改进方案（v2 — CC 审核后修订）

> 状态：**已审核，含 CC 审查意见**
> 作者：Hermes 初稿 → Claude Code 严格审查 → Hermes 修订
> 日期：2026-07-26

## 修订说明

v1 方案在「问题识别框架」上是好的，但在**事实核查**和**工作量估算**上有重大失误。
经 CC 实际读代码审查，发现 6 个严重错误，本文档为修订版。

---

## 一、CC 审查发现的 6 个事实错误（v1 → v2 修正）

| # | v1 错误陈述 | v2 修正（CC 实测） |
|---|------------|-------------------|
| 1 | 「上层只有 3 处硬编码 Cypher」 | **实际 37 处**，分布在 web router(10)/mcp_server(3)/migrations(7)/schema_version(2)/pipeline+execution(~15) |
| 2 | 「5/5 E2E 通过 = 适配可用」 | E2E 脚本只覆盖 1 个 CodeEntity + CALLS 的 mini repo；integration 测试用 mock store；**12 个实体类型 + 核心查询路径未在真实 NebulaGraph 验证** |
| 3 | （未提及 migrations） | `ontoagent migrate` 在 NebulaGraph **完全坏的**：`CREATE CONSTRAINT ... REQUIRE` 和 `MERGE ... SET` 均不支持 |
| 4 | （未提及变长路径） | `web/router/graph.py` + `mcp_server.py` 共 4 处 `MATCH path = ...-[*1..N]-`，adapter **完全不处理**，可能静默返回错误数据 |
| 5 | （未提及关系属性） | `merge_relation` 的 `properties` 参数**接收后完全不写入**，破坏 provenance + impact_propagator 的 weight 持久化 |
| 6 | 「强类型 schema +30 行」 | string→int64 是**不兼容转换**，需重建 Space 或 DROP/ADD/重写/DROP/REBUILD 全流程，是「Space 重建」级别工作量 |

## 二、4 个设计决策的反思（保留，补充 CC 发现）

### 2.1 全 string schema —— ⚠️ 应改为强类型（仅新 Space）

**现状**：`nebula_schema.py:112` 所有字段 `string`，`_format_value` 把数值也转字符串。

**代价**：
- 数值范围查询 `WHERE confidence > 0.8` 无法走索引
- 浪费 NebulaGraph 强 schema 优势

**迁移现实（CC 补充）**：
- NebulaGraph `ALTER TAG CHANGE` 不支持 string→int64（不兼容转换）
- 现有 Space 迁移需：DROP 索引 → ADD 新类型字段 → batch UPDATE 重写 → DROP 老字段 → 重建索引
- **结论：重建 Space 比迁移便宜**，前提是数据可从源码再生成；若有不可再生的 LLM 提取结果，需先做导出/导入工具

### 2.2 fallback 降级 —— ❌ 应删除，但需前置工作

**现状**：`nebula_store.py:181-192` 静默降级为 INSERT 空属性节点。

**CC 的关键追问**：直接删 fallback 会怎样？
- 理论上 schema 已补 `codeParameters`/`provenanceSource` 等字段，不再触发
- **但隐藏触发点**：`domain/schema.py` 的 `_EXTRA_FIELDS["CodeEntity"]={"lines","entryCategory"}` —— `lines` 不在 schema；其他 12 个实体的 `*_to_dict` 函数产出 key 未逐一核对

**前置工作（删 fallback 之前必须做）**：
1. 写 schema 一致性单测：遍历所有 `*_to_dict` + `add_provenance`，断言产出 key ⊆ schema 已声明字段
2. 跑一次真实 NebulaGraph 全量 build（13 个实体类型全过一遍）
3. **没有这两步就删 fallback，等于把「静默数据丢失」换成「build 间歇性崩溃」**

### 2.3 命名不一致 —— ⚠️ 统一 + 扩展登记机制

**现状**：`builder_utils.py:54` 产出 `code_parameters`，schema 有 `parameters`。

**CC 建议**：选方案 A（`entity_to_dict` 改回 `parameters`），但扩展 `_EXTRA_FIELDS` 为统一的 `ENTITY_EXTRA_FIELDS` 登记表，所有非 dataclass 反射的动态属性集中管理，避免再出现隐藏映射。

### 2.4 CypherToNgqlAdapter —— 🔄 工作量被严重低估

**v1 错误**：以为只有 3 处硬编码 Cypher。
**v2 修正**：实际 37 处，其中 4 类 adapter 完全无法处理：
- `MATCH path = ...-[*1..N]-`（变长路径，4 处）
- `MERGE ... SET`（schema_version，2 处）
- `CREATE CONSTRAINT ... REQUIRE`（migrations，7 处）
- `DETACH DELETE`（1 处）

**结论**：adapter 作为「LLM 生成查询的兜底」可保留，但**不能**作为上层 37 处硬编码 Cypher 的依赖。这 37 处需要逐个改造为语义 API 或原生 nGQL，工作量是 v1 估算的 10 倍。

## 三、CC 发现的我遗漏的 3 个高优风险

### 3.1 🔴 migrations 子系统在 NebulaGraph 上完全不工作

`schema_version.py:43` 的 `MERGE (sv:SchemaVersion {...}) SET ...` NebulaGraph 不支持 MERGE。
`migrations/v1_1_0_*.py` 的 `CREATE CONSTRAINT ... REQUIRE` NebulaGraph 没有此语法。
**`ontoagent migrate` 命令直接抛错。**

### 3.2 🔴 变长路径查询可能静默返回错误数据

`web/router/graph.py:43`（图可视化）和 `mcp_server.py:145`（影响分析）的 `MATCH path = ...-[*1..N]-`，
adapter 不处理，nGQL 虽支持 `*1..N` 但 `MATCH path = ...` + `size(labels())` + `WITH DISTINCT LIMIT` 组合经转换后语义可能不一致。
**生产环境核心 API 可能返回错误数据却不报错。**

### 3.3 🔴 关系属性完全丢失

`merge_relation` 的 `properties` 参数接收后不写入（Edge type 定义为空 `()`）。
破坏：`add_provenance()` 对关系的标记、`impact_propagator` 的 weight/affect_score 持久化、Shape 对关系属性的校验。

## 四、修订后的优先级（采纳 CC 建议）

| 优先级 | 改进项 | 理由 |
|--------|--------|------|
| **P0** | **补齐真实 NebulaGraph 集成测试**（13 实体 + 26 关系 + 4 类查询模式） | 没有 ground truth 就没资格改后续项。当前所有「通过」基于 mock 或 mini repo |
| **P0** | **migrations 子系统改造**（MERGE→UPSERT；CONSTRAINT→NebulaGraph 原生约束） | `ontoagent migrate` 当前完全坏的 |
| **P1** | 删除 fallback + **前置 schema 一致性单测** | 原 P0，后置到 P0 完成 |
| **P1** | 统一命名 + 扩展 `ENTITY_EXTRA_FIELDS` 登记 | — |
| **P1** | **关系属性支持**（Edge type 加属性，恢复 provenance/weight 持久化） | v1 完全遗漏，影响 impact_propagator 正确性 |
| **P2** | 变长路径 + 37 处硬编码 Cypher 改造（语义 API 或原生 nGQL） | 高 ROI 但工作量是 v1 估算的 10 倍 |
| **P3** | 强类型 schema（仅新 Space；现有环境走 Space 重建） | 迁移成本 = 重建 Space 级别 |

## 五、教训

1. **搜索 pattern 要宽**：v1 用 `.query("MATCH` 漏掉了 f-string 拼接的 Cypher，导致工作量估算偏差 10 倍
2. **「测试通过」≠「功能可用」**：必须追问测试到底覆盖了什么，E2E 脚本的 mini repo 不能代表全量
3. **修 bug 时要系统排查同类问题**：只盯着 Phase 4 的 5 个 bug，漏掉了 migrations、变长路径、关系属性这些没被测试触发的路径
4. **删容错前先建 ground truth**：否则只是把一种失败模式换成另一种更难定位的
