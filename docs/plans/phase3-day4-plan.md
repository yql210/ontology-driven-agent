# Phase 3 Day 4 计划 — 自建评估集 25 题 + 完整评估

## 目标
1. 创建 25 道评估题（L1:10, L2:10, L3:5），覆盖全部 8 个 Agent 工具
2. 编写异步评分脚本 `run_eval.py`
3. 跑完整 25 题评估 → 生成报告
4. 根据弱项制定优化策略

## Neo4j 数据分布（已验证 2025-05-11）
- CodeEntity: 312
- ConceptEntity: 54
- ModuleEntity: 16
- CONTAINS: 549, IMPORTS: 255, DERIVED_FROM: 118, CALLS: 109, SEMANTIC_IMPACT: 94, EXTENDS: 6

## 真实实体名（用于构造题目）
- CodeEntity: ConceptAligner, AlignResult, SHA256Cache, GitChangeDetector, ChangedFile, Neo4jGraphStore, PythonParser, ChromaVectorStore, SemanticExtractor, RelationExtractor, ImpactPropagator, ModuleClustering, Builder, CLI
- ConceptEntity: Pipeline Pattern, Cache Pattern, Strategy Pattern, Semantic Search, Repository Pattern, Builder Pattern, Adapter Pattern, Iterator Pattern, Data Model, Data Validation
- ModuleEntity: layerkg, aligner.py, change_detector.py, chroma_store.py, cli.py, extractor, relation.py, graph_store.py
- CALLS 关系: ConceptAligner._exact_match → AlignResult, SHA256Cache.set → _CacheEntry, GitChangeDetector.full_scan → ChangedFile

## Task 拆分

### Batch 1: 评估框架（~3 min）
**Task 1.1**: 创建 `tests/evaluation/__init__.py` 和 `tests/evaluation/questions.json`
- 25 道题的 JSON 数组，格式：
```json
[
  {
    "id": "L1-001",
    "level": "L1",
    "category": "semantic_search",
    "question": "搜索与缓存相关的代码",
    "expected_keywords": ["SHA256Cache", "cache", "_CacheEntry"],
    "expected_tool": "semantic_search",
    "scoring": "keyword_match",
    "min_score": 0.5
  }
]
```
- L1（10 题）：单工具调用，精确匹配
- L2（10 题）：多工具组合，需推理
- L3（5 题）：开放分析，需综合多个数据源

**Task 1.2**: 创建 `tests/evaluation/run_eval.py`
- 异步评分脚本，逐题调用 Agent（`create_agent()` + `ainvoke()`）
- 评分逻辑：关键词匹配（`expected_keywords` 中命中 ≥60% 得满分，≥30% 得半分）
- 输出 JSON 报告到 `tests/evaluation/eval_report.json`
- CLI 参数：`--questions L1|L2|L3|all`，`--limit N`，`--verbose`
- 每题设置 60 秒超时（避免 LLM 无限循环）
- 支持断点续跑（已完成题目跳过）

**Task 1.3**: 创建 `tests/evaluation/test_eval_format.py`
- 验证 questions.json 格式正确
- 验证每题有 id/level/category/question/expected_keywords/expected_tool
- 验证 L1=10, L2=10, L3=5

### Batch 2: 25 题设计（~5 min）

**L1 — 单工具调用（10 题）**:

| ID | 工具 | 问题 | 关键词 |
|---|---|---|---|
| L1-001 | semantic_search | 搜索与缓存相关的代码 | SHA256Cache, cache, _CacheEntry |
| L1-002 | graph_query | ConceptAligner 类有哪些方法 | ConceptAligner, align, _exact_match, _alias_match |
| L1-003 | graph_query | 查找所有概念实体的名称 | Pipeline Pattern, Cache Pattern, Strategy Pattern |
| L1-004 | impact_analysis | 分析修改 ConceptAligner.align 的影响 | ConceptAligner, align, impact |
| L1-005 | get_context | 获取 ConceptAligner 的上下文信息 | ConceptAligner, class, align |
| L1-006 | list_concepts | 列出所有已注册的概念 | Pipeline Pattern, Cache Pattern, Semantic Search |
| L1-007 | get_module_tree | 获取项目的模块结构树 | layerkg, module |
| L1-008 | graph_query | Neo4jGraphStore 继承自什么类 | GraphStore, EXTENDS |
| L1-009 | semantic_search | 搜索与语义提取相关的代码 | SemanticExtractor, semantic, extract |
| L1-010 | graph_query | 查找 CONTAINS 关系数量最多的模块 | CONTAINS, module |

**L2 — 多工具组合（10 题）**:

| ID | 工具 | 问题 | 评估标准 |
|---|---|---|---|
| L2-001 | get_context + graph_query | ConceptAligner 调用了哪些函数？被谁调用？ | CALLS 双向关系 |
| L2-002 | impact_analysis + get_context | 修改 SHA256Cache 的影响范围，涉及哪些模块？ | SHA256Cache + 影响链 |
| L2-003 | list_concepts + graph_query | "Cache Pattern" 概念关联了哪些代码实体？ | DESCRIBES 或 DERIVED_FROM |
| L2-004 | get_module_tree + graph_query | cli.py 模块包含哪些实体？它们的调用关系？ | CONTAINS + CALLS |
| L2-005 | semantic_search + get_context | 找到与向量搜索相关的代码，获取其详细信息 | ChromaVectorStore, search |
| L2-006 | graph_query + impact_analysis | PythonParser 被谁依赖？修改它会影响什么？ | IMPORTS/CALLS 入边 + 影响传播 |
| L2-007 | get_module_tree + list_concepts | 各模块分别涉及哪些设计模式或概念？ | Module → Concept 映射 |
| L2-008 | graph_query × 2 | 项目中有哪些类继承了其他类？列出继承关系 | EXTENDS, 6条 |
| L2-009 | semantic_search + graph_query | 搜索 "change detection" 相关代码，分析其调用链 | GitChangeDetector, full_scan, detect |
| L2-010 | get_context + impact_analysis | RelationExtractor 的详细信息和变更影响分析 | RelationExtractor, relation, extract |

**L3 — 开放分析（5 题）**:

| ID | 问题 | 评估标准 |
|---|---|---|
| L3-001 | 请总结 LayerKG 项目的整体架构，包括核心模块和它们之间的依赖关系 | 覆盖 builder/extractor/store/parser/agent 五大模块 |
| L3-002 | 如果要给项目添加一个新的解析器（如 Java Parser），需要修改哪些文件？会影响到哪些现有功能？ | 识别 parser/ 抽象层 + 继承关系 + 影响分析 |
| L3-003 | 项目中哪些模块的耦合度最高？请根据调用关系和依赖关系分析 | CALLS + IMPORTS 密集度分析 |
| L3-004 | Cache Pattern 和 Repository Pattern 在项目中是如何体现的？具体关联了哪些代码？ | 概念→代码实体的 DERIVED_FROM/DESCRIBES |
| L3-005 | 分析 chroma_store.py 模块的内部结构和外部依赖关系 | CONTAINS + CALLS + IMPORTS 全链路 |

### Batch 3: 执行评估 + 报告（~15-25 min）
**Task 3.1**: 先跑 L1 的 10 题验证评分脚本
**Task 3.2**: 跑 L2 的 10 题
**Task 3.3**: 跑 L3 的 5 题
**Task 3.4**: 生成完整评估报告

## 评分规则
- **L1**: 关键词命中率 ≥60% → 1.0 分，≥30% → 0.5 分，<30% → 0.0 分
- **L2**: 关键词命中率 ≥50% → 1.0 分，≥25% → 0.5 分，<25% → 0.0 分（允许多工具组合部分命中）
- **L3**: 关键词命中率 ≥40% → 1.0 分，≥20% → 0.5 分，<20% → 0.0 分（开放题更宽松）
- **额外分**: 正确使用预期工具（expected_tool）+0.2 加分
- **超时**: 0.0 分

## 报告格式
```json
{
  "timestamp": "2025-05-11T...",
  "total_questions": 25,
  "results": [
    {
      "id": "L1-001",
      "question": "...",
      "level": "L1",
      "agent_answer": "...",
      "tools_used": ["semantic_search"],
      "score": 1.0,
      "keyword_hits": ["SHA256Cache", "cache"],
      "keyword_misses": [],
      "latency_seconds": 3.2,
      "error": null
    }
  ],
  "summary": {
    "L1_accuracy": 0.8,
    "L2_accuracy": 0.6,
    "L3_accuracy": 0.4,
    "overall_accuracy": 0.64,
    "avg_latency": 5.3,
    "tool_usage": {"semantic_search": 5, "graph_query": 12, ...},
    "failure_categories": {"timeout": 1, "wrong_tool": 2, "keyword_miss": 6}
  }
}
```

## 风险与缓解
1. **glm-4-flash 可能不理解复杂查询** → L3 题允许更宽松的关键词匹配
2. **Agent 超时** → 每题 60s 超时，超时标记 0 分继续
3. **LLM API 限流** → 题间加 2s 延迟
4. **关键词匹配过严** → 使用子串匹配（CONTAINS），非精确匹配

## 验收标准
- [ ] 25 题评估集 questions.json 格式正确
- [ ] run_eval.py 可独立运行
- [ ] test_eval_format.py 全部通过
- [ ] 完整 25 题评估报告已生成
- [ ] 识别弱项并有优化方向
