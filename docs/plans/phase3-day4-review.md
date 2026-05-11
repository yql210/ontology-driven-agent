# Phase 3 Day 4 实施计划审核报告

**审核人**: 高级架构师  
**审核日期**: 2026-05-11  
**计划文件**: `docs/plans/phase3-day4-plan.md`

---

## 总评分：7.8 / 10（有结构性问题，需修订后执行）

> ⚠️ **关键发现**: 计划中描述的 `questions.json` + `run_eval.py` 设计，**实际代码库中已有更成熟的实现** (`eval_set.json` + `run_eval.py` + `test_eval_set.py` + `test_run_eval.py`)。现有实现已跑过 2 题验证（eval_report.json），设计优于计划文档。

---

## 一、逐项审核

### 1. 25 道评估题设计审核

#### 1.1 计划中的题目设计（docs/plans/phase3-day4-plan.md）

**评分: 6.5/10 — 存在多个数据不匹配问题**

| 问题 | 严重度 | 说明 |
|------|--------|------|
| L1-003 关键词只列3个ConceptEntity | 🟡中 | 实际有54个，预期关键词太少，3/54=5.5%命中率太低，60%阈值根本达不到 |
| L1-007 "GitChangeDetector 类有哪些方法" 用 CONTAINS | 🔴严重 | **GitChangeDetector 类与其方法之间没有 CONTAINS 关系**。方法以 `GitChangeDetector.xxx` 命名，但不被 CONTAINS 连接。Agent 需要用 `n.name STARTS WITH 'GitChangeDetector.'` 查询，这是一个非直觉的 Cypher，glm-4-flash 大概率写不对 |
| L1-008 "Neo4jGraphStore 继承自什么类" | ✅正确 | 验证了 EXTENDS 关系确实存在 `Neo4jGraphStore -> GraphStore` |
| L1-009 "查找 CONTAINS 关系数量最多的模块" | ✅正确 | module_0 有69个，确实是最大 |
| L1-004 "分析修改 ConceptAligner.align 的影响" | ✅正确 | 实体存在，impact_analysis 可用 |
| L2-003 "Cache Pattern 关联了哪些代码实体" 用 DESCRIBES | 🔴严重 | **DESCRIBES 关系数量为 0**！应该是 `DERIVED_FROM`（118条）。计划中的 "DESCRIBES 或 DERIVED_FROM" 会导致 Agent 查 DESCRIBES 空结果 |
| L2-004 "cli.py 模块包含哪些实体？它们的调用关系" | 🟡中 | cli.py 确实只有 5 个实体（build, update, info, serve, main），太少，题目区分度低 |
| L2-005 "搜索与向量搜索相关的代码" 预期 ChromaVectorStore | 🔴严重 | **ChromaVectorStore 不存在**！实际类名是 `ChromaStore` |
| L2-008 "项目中哪些类继承了其他类" 预期 "6条" | ✅正确 | 确实6条 EXTENDS |
| L3-003 "耦合度最高" | 🟡中 | 需要多次 Cypher 聚合查询，glm-4-flash 写复杂聚合能力有限 |
| L3-004 "Cache Pattern 和 Repository Pattern" | ✅正确 | Cache Pattern 有 7 条 DERIVED_FROM，Repository Pattern 应该也有 |

**覆盖面分析**:
- ✅ 8个工具覆盖: semantic_search(3), graph_query(8), impact_analysis(2), get_context(3), list_concepts(2), get_module_tree(2), **detect_changes(0)**, **export_graph(0)**
- 🔴 **detect_changes 和 export_graph 完全没有覆盖**！这是严重的覆盖缺口

#### 1.2 现有 eval_set.json 的题目设计

**评分: 8.5/10 — 明显优于计划，但仍有问题**

| 优点 | 说明 |
|------|------|
| ✅ 包含 validation.cypher | 可以对答案做独立验证，比纯关键词匹配更客观 |
| ✅ 4种 answer type | exact/contains/list/fuzzy 分层合理 |
| ✅ detect_changes(L2-009) 被覆盖 | 计划中遗漏的工具被纳入 |
| ✅ export_graph(L1-010) 被覆盖 | 计划中遗漏的工具被纳入 |
| ✅ 每题有 validation 字段 | 支持事后交叉验证 |

| 问题 | 说明 |
|------|------|
| L1-007 用 CONTAINS 验证 GitChangeDetector 子方法 | 🔴 同样的 CONTAINS 问题。Cypher 用 `MATCH (c:CodeEntity {name: 'GitChangeDetector'})-[:CONTAINS]->(m:CodeEntity)`，但 GitChangeDetector 不是一个 ModuleEntity，它的方法不是通过 CONTAINS 关联的。**这个 cypher 会返回空结果** |
| L2-003 ChromaStore 的 CONTAINS | 🔴 `MATCH (c:CodeEntity {name: 'ChromaStore'})-[:CONTAINS]->(m:CodeEntity)` 同样的问题，ChromaStore 不是 ModuleEntity，其方法不以 CONTAINS 关联 |
| L2-008 RelationExtractor 的 CONTAINS | 🔴 同上 |
| L2-005 预期 "258" | 🟡 依赖于精确 file_path 前缀匹配，但 Agent 可能给出不同表述。exact type 评分太严 |
| L3-003 keywords 包含 "agent" | 🟡 agent 模块确实存在但可能不被 ModuleEntity 覆盖 |
| level 用整数 1/2/3 | ✅ 比计划中的 "L1"/"L2"/"L3" 字符串更实用 |

### 2. 评分规则审核

#### 2.1 计划中的评分规则

**评分: 7/10 — 不够精细**

- L1/L2/L3 不同阈值（60%/50%/40%）是合理的
- 但"关键词命中率"存在问题：
  - 计划用子串匹配（CONTAINS），但未说明大小写处理
  - L1 预期关键词只有 2-3 个，少一个就直接降到半分/零分，区分度差
  - 额外分 +0.2 的"正确使用预期工具"会导致总分超过 1.0

#### 2.2 现有 run_eval.py 的评分规则

**评分: 8/10 — 更合理**

- L1: 工具匹配 30% + 答案匹配 70%（侧重答案正确性）✅
- L2: 工具匹配 40% + 答案匹配 60%（工具权重提升）✅
- L3: 工具匹配 50% + 答案匹配 50%（开放题工具更重要）✅
- "correct" 阈值 score >= 0.7，合理

**但存在问题**:
- 工具匹配是二元的（全匹配/不匹配），没有"部分匹配"中间态。L2/L3 预期多个工具，只用了其中一半也应该给部分分
- exact 类型实际上也是 contains（`expected_value in actual_lower`），名为 exact 但行为不是精确匹配
- 30 秒超时（代码中 `timeout_sec: int = 30`）**但 Agent graph.py 中有 120 秒 wait_for**。两层超时不一致，run_eval.py 的 30s 超时实际上没有生效（因为 `run_single_question` 没有用 `asyncio.wait_for` 包装）
- 缺少题间延迟（计划中提了 2s 延迟，代码中没实现）

### 3. run_eval.py 设计审核

**评分: 8/10 — 基本可行，有几个 Bug**

#### 已确认可行的部分
- ✅ `create_agent()` + `ainvoke()` 调用方式正确
- ✅ 消息提取逻辑（从 result["messages"] 中提取工具调用和最终回答）正确
- ✅ CLI 参数设计合理（--questions, --limit, --eval-set, --output）
- ✅ 报告 JSON 格式完整

#### 需修复的问题

| 问题 | 严重度 | 说明 |
|------|--------|------|
| **超时未实际生效** | 🔴严重 | `run_single_question` 的 `timeout_sec` 参数声明了但未使用。需要加 `asyncio.wait_for(coro, timeout=timeout_sec)` |
| **msg.dict() 兼容性** | 🟡中 | LangChain 新版本中 `msg.dict()` 已废弃，应改用 `msg.model_dump()` 或 `msg.to_json()` |
| **无断点续跑** | 🟡中 | 计划中提到支持断点续跑，但代码中没有实现。如果跑到 L2 超时崩溃，需从头来 |
| **无题间延迟** | 🟢低 | 缺少 2s 延迟，可能触发 API 限流 |
| **缺少 verbose 模式** | 🟢低 | 计划提到 --verbose 参数但代码没实现 |

### 4. 预期关键词与 Neo4j 实际数据匹配度

**评分: 7/10 — 多处不匹配**

| 计划中的实体/关键词 | Neo4j 实际数据 | 匹配? |
|---------------------|----------------|-------|
| SHA256Cache, cache, _CacheEntry | ✅ 存在 | ✅ |
| ConceptAligner, align, _exact_match, _alias_match | ✅ 存在 | ✅ |
| Pipeline Pattern, Cache Pattern, Strategy Pattern | ✅ 存在 | ✅ |
| ChromaVectorStore | ❌ 不存在（实际 ChromaStore） | 🔴 |
| DESCRIBES 关系 | ❌ 0 条（应用 DERIVED_FROM） | 🔴 |
| SemanticExtractor, semantic, extract | ✅ 存在 | ✅ |
| GraphStore（作为 EXTENDS 目标） | ✅ 存在（CodeEntity 标签） | ✅ |
| GitChangeDetector, full_scan, detect | ✅ 存在 | ✅ |
| RelationExtractor, relation, extract | ✅ 存在 | ✅ |

### 5. 遗漏的边界情况

**评分: 6/10 — 多个边界未覆盖**

| 边界情况 | 说明 |
|----------|------|
| **Agent 返回空回答** | 如果 LLM 生成了 tool_calls 但最终没生成文本回答，`actual_answer` 为空串 |
| **Agent 工具调用失败** | graph_query 返回 Cypher 错误，Agent 可能直接把错误信息当答案返回 |
| **MemorySaver 跨题污染** | 每题用不同 thread_id，但 MemorySaver 是全局单例，如果有状态泄漏会互相影响 |
| **ChromaDB/Ollama 不可用** | semantic_search 工具会返回 error JSON，Agent 如何处理？没有测试这种降级场景 |
| **超大回答截断** | 代码中截断到 500 字符，但关键词可能出现在 500 字之后 |
| **LLM 重复调用同一工具** | Agent 可能陷入循环调用同一工具（虽然有 recursion_limit=50） |
| **checkpointer 积累** | 25 题后 MemorySaver 会积累大量状态，可能导致后续调用变慢 |

---

## 二、结论与建议

### 关键决策：应基于现有代码而非计划文档执行

现有代码（eval_set.json + run_eval.py）已经比计划文档更完善：
- 有 Cypher 验证字段（比纯关键词匹配更客观）
- 覆盖了 detect_changes 和 export_graph（计划遗漏了这两个工具）
- 评分体系（工具+答案加权）比纯关键词匹配更合理
- 已有测试文件（test_eval_set.py + test_run_eval.py）

### 必须修改的条目（blocking）

1. **修复 CONTAINS 误用**：L1-007、L2-003、L2-008 的 validation.cypher 错误使用 `(class)-[:CONTAINS]->(method)` 模式。应改为：
   ```cypher
   MATCH (n:CodeEntity) WHERE n.name STARTS WITH 'ClassName.' RETURN n.name
   ```
   或使用 CONTAINS 来自 ModuleEntity 而非 CodeEntity。

2. **修复超时未生效**：`run_single_question` 中添加 `asyncio.wait_for` 包装：
   ```python
   result = await asyncio.wait_for(
       agent.ainvoke(...),
       timeout=timeout_sec
   )
   ```

3. **DESCRIBES → DERIVED_FROM**：计划 L2-003 提到 DESCRIBES，但数据中不存在。eval_set.json 中已正确使用 DERIVED_FROM，确认无需修改。

### 建议修改的条目（recommended）

4. **添加工具部分匹配评分**：L2/L3 的 `calculate_tool_match` 应改为比例匹配
5. **添加题间 2s 延迟**：避免 API 限流
6. **实现断点续跑**：加载已有 report，跳过已完成 ID
7. **answer 截断长度提升到 2000**：避免遗漏尾部关键词
8. **L2-005 从 exact 改为 contains**："258" 这个精确值太严格

### 执行路径建议

1. 修复 eval_set.json 中 3 个 CONTAINS 误用的 cypher
2. 修复 run_eval.py 超时 bug
3. 跑 test_eval_set.py + test_run_eval.py 确认通过
4. 分批执行：L1(10题) → 检查 → L2(10题) → 检查 → L3(5题)
5. 生成报告 + 弱项分析

---

## 三、评分汇总

| 维度 | 评分 | 说明 |
|------|------|------|
| 题目设计 | 6.5/10 | 关键词/关系多处不匹配，2个工具未覆盖 |
| 评分规则 | 8/10 | 现有加权方案合理，但工具匹配缺粒度 |
| run_eval.py | 8/10 | 基本可行，超时 bug 需修 |
| 关键词匹配度 | 7/10 | 2处实体名错误，1处关系类型错误 |
| 边界覆盖 | 6/10 | 缺少空回答、工具失败、降级等测试 |
| **综合** | **7.8/10** | **需修订后执行** |
