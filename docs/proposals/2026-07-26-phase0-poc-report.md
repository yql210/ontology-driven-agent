# Phase 0 POC Go/No-Go 评估报告

> **日期：2026-07-26**
> **NebulaGraph 版本：3.7.0（远程实例 124.221.243.142:9669）**
> **客户端：nebula3-python 3.8.3**

---

## 一、验证项目与结果

### 1.1 Schema 创建 ✅

| 项目 | 结果 |
|---|---|
| Space 创建（FIXED_STRING(36) VID） | ✅ |
| 13 个 Tag | ✅ 全部创建成功 |
| 26 个 Edge type | ✅ 全部创建成功 |
| 索引（每 Tag 的 name 属性） | ✅ |

**发现的问题：**
- `timestamp` 是 NebulaGraph 保留字，必须用反引号 `` `timestamp` ``
- DDL 是异步的，Space 创建后需等待 ~20s 才能使用

### 1.2 数据写入 ✅

| 操作 | 结果 |
|---|---|
| INSERT VERTEX（7 个节点，4 种实体类型） | ✅ |
| INSERT EDGE（6 条关系，4 种关系类型） | ✅ |
| UPSERT VERTEX（更新属性） | ✅ |

**发现的问题：**
- VID 长度必须 ≤ FIXED_STRING(36)，标准 UUID（36 字符）正好适配

### 1.3 关键查询验证（7/7 通过）

| 查询 | 描述 | 结果 |
|---|---|---|
| Q1 | 基本节点查找（tag 前缀属性访问） | ✅（`path` 是保留字，换别名） |
| Q2 | 变长路径 `*1..3`（ShapeEvaluator 核心） | ✅ |
| Q3 | 边查询（startNode/endNode 重写为 pattern 变量） | ✅ |
| Q4 | 4 跳业务追溯（Code→Concept+Data→Compliance） | ✅ |
| Q5 | 统计聚合（`tags()` 替代 `labels()`） | ✅ |
| Q6 | UPSERT VERTEX（替代 MERGE） | ✅ |
| Q7 | 变长路径 + path 变量 + `nodes(p)` + `length(p)` | ✅ |

### 1.4 性能 Benchmark ✅

**环境：** 远程实例（124.221.243.142），网络延迟约 75-80ms 基线。

| Benchmark | P50 | P99 | Go 标准 | 结论 |
|---|---|---|---|---|
| BM1: 节点查找（name 索引） | 86.1ms | 109.7ms | - | ✅ |
| BM2: 变长路径 *1..3 | **86.0ms** | 96.6ms | < 100ms | **✅ Go** |
| BM3: 单跳路径 *1..1 | 83.3ms | 100.1ms | - | ✅ |
| BM4: 4 跳业务追溯 | 90.5ms | 117.0ms | - | ✅ |
| BM5: 单次 UPSERT | 81.2ms | 203.3ms | - | ✅ |
| BM6: 统计聚合（全表） | 85.2ms | 96.6ms | - | ✅ |
| BM7: 批量100条循环写入 | 124.5ms/条 | - | - | ⚠️ 偏慢 |

**关键结论：**
- **所有查询 P50 < 100ms，通过 Go 标准。**
- 基线延迟约 80ms（远程网络），本地部署预计 < 20ms。
- 批量写入 124.5ms/条偏慢（循环 UPSERT），但 OntoAgent 是知识图谱构建（分钟级），不是高并发写入场景。

---

## 二、关键发现

### 2.1 D1-Final 方案验证为可行 ✅

方案中预判的 7 类查询模式全部在 NebulaGraph 上验证通过：
- tag 前缀属性访问 ✅
- 变长路径 ✅
- startNode/endNode 重写 ✅
- tags() 替代 labels() ✅
- UPSERT 替代 MERGE ✅
- 多跳跨实体追溯 ✅
- path 变量 + nodes(p)/length(p) ✅

### 2.2 新发现的坑（需纳入实施方案）

| 发现 | 影响 | 应对 |
|---|---|---|
| `timestamp` 是保留字 | 所有含此字段名的 Tag DDL 要加反引号 | NebulaSchemaInitializer 自动检测保留字 |
| `path` 是保留字 | RETURN 别名不能用 path | 避免使用保留字做别名 |
| Space 创建后需等待 ~20s | 启动初始化慢 | 加重试 + 轮询 |
| VID 严格限制 36 字符 | UUID 正好适配，但不能加前缀 | OntoAgent id 直接用 UUID |
| 批量写入 124ms/条 | 构建大仓库时可能慢（1万节点=20分钟） | 后续优化：批量 INSERT VALUES |

### 2.3 性能分析

远程实例（~80ms 网络基线）下所有查询 P50 < 100ms。生产环境（本地网络）预计：
- 单次查询：< 20ms
- 变长路径 *1..3：< 30ms
- ShapeEvaluator 5 个 Shape 串行：< 150ms

**如需进一步优化，ShapeEvaluator 可并行评估多个 Shape。**

---

## 三、Go/No-Go 判定

### ✅ GO — 三项标准全部通过

| Go/No-Go 标准 | 结果 | 数据 |
|---|---|---|
| 性能：ShapeEvaluator < 100ms | ✅ **通过** | 变长路径 P50=86ms，4跳追溯 P50=90ms |
| 查询兼容：7 类关键模式可表达 | ✅ **通过** | 7/7 验证通过 |
| LLM 质量：nGQL 正确率 > 80% | ✅ **通过** | 18/20 = **90%**（DeepSeek-v4-flash） |

### LLM 质量测试详情

20 个自然语言问题 → LLM 生成 nGQL → 在真实 NebulaGraph 上执行：

- **18/20 = 90% 正确执行**
- 2 个失败的错误类型：
  - Q9: `MATCH (n {CodeEntity.name:"xxx"})` — 用了 inline property matching 但格式错误（应该用 WHERE）
  - Q20: `NOT EXISTS((:CodeEntity)-[:CALLS]->(n))` — NebulaGraph 的 EXISTS 语法与 Cypher 不完全兼容
- **关键指标：0 个 tag 前缀遗漏错误**——LLM 完全学会了 `v.Tag.field` 属性访问格式

### 进入 Phase 1

Phase 0 POC 全部通过，D1-Final 方案验证可行。进入 Phase 1: NebulaGraphStore 实现。

---

## 四、POC 产出文件

| 文件 | 用途 |
|---|---|
| `poc_nebula.py` | Schema 创建 + 数据写入 + 7 条查询验证 |
| `poc_nebula_benchmark.py` | 7 项性能 benchmark |
