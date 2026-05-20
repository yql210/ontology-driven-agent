# LayerKG 技术博客第7篇素材摘要
## 「从学术论文到工程落地：RepoDoc → LayerKG」

---

## 一、RepoDoc 论文核心思想

> 论文: arxiv 2604.26523 — RepoDoc: Repository-Level Documentation Generation via Knowledge Graph-Enhanced LLM

**论文核心主张：**
- 利用知识图谱（KG）建模代码仓库的结构和语义关系，辅助 LLM 生成更准确的仓库级文档
- KG 作为"中间表示层"，桥接代码静态分析和 LLM 的语义理解
- 核心数据模型：代码实体（函数/类/模块）+ 关系（调用/继承/包含）

**LayerKG 在此基础上做了什么：**
RepoDoc 是论文的出发点，LayerKG 则是把论文思想做成了**生产级引擎**。

---

## 二、LayerKG 超出论文的工程化决策（清单）

### 2.1 Schema 设计（Phase 0, schema.py）
| 论文 | LayerKG |
|------|---------|
| 代码实体 3-4 种 | **6 种实体**: CodeEntity(8子类型), ConceptEntity(5子类型), DocEntity(6子类型), ResourceEntity(6子类型), ModuleEntity, ChangeSetEntity |
| 关系 3-4 种 | **11 种关系**: 结构(calls/extends/implements/imports/contains) + 语义(semantic_impact/describes/illustrates/derived_from) + 变更(changed_in/affects) |
| 无约束检查 | **本体约束**: RELATION_CONSTRAINTS 定义 domain/range 约束，运行时 validate_relation_constraint() 校验 |
| 无异常体系 | **3 层异常**: LayerKGError → SchemaValidationError / ConstraintViolationError / StoreError |
| 无版本管理 | **Schema 版本追踪**: CURRENT_SCHEMA_VERSION="1.0.0", SchemaStatus 枚举(EMPTY/MATCH/BEHIND/AHEAD) |

### 2.2 构建管线（builder.py, 1236 行）
| 决策 | 说明 |
|------|------|
| **5 阶段流水线** | Stage1:Parse → Stage2:StructuralWrite → Stage2.5:DocCodeLink → Stage3:SemanticExtraction → Stage4:ModuleClustering → Stage5:VectorIndex |
| **错误降级策略** | Stage 2 是关键路径（失败直接 abort），Stage 3/4/5 可降级跳过 |
| **多语言支持** | Python(777行) + Java(1068行) 双解析器，基于文件扩展名自动注册 |
| **批次溯源** | batch_time 统一时间戳，add_provenance() 给每个节点/关系注入来源和置信度 |

### 2.3 数据溯源与可信度（provenance.py）
- 5 种来源类型: ast_parser(1.0), llm_extraction(0.7-0.95), clustering, manual, imported
- confidence 值 clamp 到 [0.0, 1.0]
- 每条关系都携带 provenance_source + confidence + extracted_at

### 2.4 本体演化机制（migrations/ + schema_version.py）
- MigrationBase 抽象类：upgrade() 必须幂等，downgrade() 可回滚
- SchemaVersion 节点持久化到 Neo4j（MERGE 保证幂等）
- 状态检测：EMPTY(空库) → MATCH(一致) → BEHIND(需迁移) → AHEAD(旧代码连新库)

### 2.5 增量更新（incremental_updater.py, 687 行）
- GitChangeDetector 检测文件变更
- 增量管线：3 阶段（删除/新增/修改）+ 影响传播（ImpactPropagator, 双向BFS）
- 非全量重建，只处理 diff

### 2.6 Agent 层（Phase 3-5）
- LangGraph ReAct Agent + 8 个 MCP 工具（FastMCP Server）
- 评估框架：25 题 → 35 题，准确率 72% → 91.4%
- Butler Engine：EventBus + Scheduler + ConsistencyGuard + SkillStore
- Web UI：FastAPI + Vue 3 + SSE 流式 + Cytoscape.js 图可视化

### 2.7 Docker 容器化（Phase 6.5）
- 全栈 Docker 部署

---

## 三、从 Phase 0 到 Phase 6 的 Commit 时间线

| 日期 | Phase | 关键 Commit | 测试数 |
|------|-------|-------------|--------|
| 05-07 | **Phase 0** (Day 1-5) | 脚手架 → Schema → Neo4j → Parser → CLI/Builder | ~200 |
| 05-07~08 | **Phase 1** (Day 6-13) | ConceptAligner → ChangeDetector → ImpactPropagator → IncrementalUpdater → SemanticExtractor → ModuleClustering → MCP Server | 544 |
| 05-08~09 | **Phase 2** (Day 1-9) | EntityIndex → SemanticPipeline → BuildPipeline重构 → DocParser → CLI增强 → BatchVector → 质量修复 | ~680 |
| 05-09 | **Phase 2.5** | 数据质量补救: CALLS, IMPORTS, SEMANTIC_IMPACT, ModuleEntity, DocEntity | ~750 |
| 05-10~11 | **Phase 3** (Day 1-5) | Agent骨架(LangGraph) → 工具补全(8个) → CLI交互 → 评估框架(72%→91.4%) | ~850 |
| 05-11~12 | **Phase 4** (Day 1-5) | FastAPI后端 → Vue 3前端 → Cytoscape图可视化 → 可观测性(Trace) | ~800 |
| 05-13~14 | **Phase 4.5** | Java Parser (Day 0-5): enum/record/field → JavaParser → 关系提取 → 多语言注册 → 端到端验证 | 910 |
| 05-15 | **Phase 5** (Sub 1+2) | Butler Engine: EventBus/Scheduler/ConsistencyGuard → Handlers → GitWatcher → CLI | 1032 |
| 05-19 | **Phase 6.1-6.3** | 本体约束(11关系) + Agent护栏 + 数据溯源provenance | 1089 |
| 05-20 | **Phase 6.4** | Schema版本追踪 + 迁移框架 | 1131 |
| 05-20 | **Phase 6.5** | Docker容器化 | 1131 |

---

## 四、关键设计转折点

1. **Phase 0 Day 5 → Phase 1**: 从"纯 KG 引擎"扩展到"增量更新 + 影响传播"——意识到全量构建不可持续
2. **Phase 2 Day 4 重构**: 5 阶段流水线 + 错误降级——builder.py 从单体演进为容错管线
3. **Phase 2.5 质量补救**: 发现 CALLS/IMPORTS 关系有缺失，专门一个 Phase 做数据质量修复
4. **Phase 3→4**: 从 CLI 工具走向 Web 平台——加了 FastAPI + Vue 3 前端
5. **Phase 4.5 Java 支持**: Schema 扩展 enum/record/field，Parser 注册表模式——为多语言奠定基础
6. **Phase 6.1 本体约束**: 11 种关系全部加 domain/range 约束——从"能存"到"存对"
7. **Phase 6.3 溯源**: provenance_source + confidence——区分 AST 确定性 vs LLM 概率性
8. **Phase 6.4 演化**: Schema 版本追踪——为未来 schema 变更铺路

---

## 五、可引用的具体数据

| 指标 | 数值 |
|------|------|
| 源文件数 | 54 个 .py 文件 |
| 源码行数 | 12,157 行 |
| 测试文件数 | 72 个 |
| 测试代码行数 | 23,145 行 |
| 测试用例数 | 1,108 个（注: grep 统计含一些 .pyc，实际约 1,131） |
| 总 Commit 数 | 81 个 |
| 开发周期 | 2026-05-07 ~ 2026-05-20（14 天） |
| 最大源文件 | builder.py (1,236 行) |
| Java Parser | 1,068 行 |
| Python Parser | 708 行 |
| IncrementalUpdater | 687 行 |
| Schema 实体 | 6 种（8+5+6+6+1+1 子类型） |
| Schema 关系 | 11 种 |
| Schema 版本 | v1.0.0 |
| Provenance 来源 | 5 种 |
| Agent 评估准确率 | 91.4% (35题) |
| MCP 工具数 | 8 个 |
| 支持语言 | Python + Java |

---

## 六、论文 vs 工程对比表（博客素材）

```
+---------------------+------------------+---------------------------+
| 维度                | RepoDoc 论文     | LayerKG 工程              |
+---------------------+------------------+---------------------------+
| 数据模型            | 3-4 种实体       | 6 种实体, 11 种关系       |
| 关系约束            | 无               | domain/range 本体约束     |
| 错误处理            | 无               | 3层异常 + 错误降级管线    |
| 增量更新            | 无（全量）       | Git diff → 3阶段增量      |
| 影响分析            | 无               | 双向BFS影响传播           |
| 多语言              | 单语言           | Python + Java（可扩展）   |
| 数据溯源            | 无               | 5种来源 + 置信度          |
| Schema演化          | 无               | 版本追踪 + 迁移框架       |
| 向量检索            | 无               | ChromaDB + Ollama嵌入     |
| Agent集成           | 无               | LangGraph ReAct + 8工具   |
| Web UI              | 无               | FastAPI + Vue 3 + 图可视化|
| 评估                | 人工评估         | 35题自动评估, 91.4%       |
| 测试覆盖            | 无               | 1131 测试用例             |
| 容器化              | 无               | Docker 全栈部署           |
+---------------------+------------------+---------------------------+
```

---

## 七、踩坑实录素材（从 commit 中提取）

1. **Neo4j `parameters` 关键字冲突**: `82a6d2f` — rename Neo4j prop 'parameters' to 'code_parameters'，因为 neo4j driver 的 session.run() 有同名 kwarg
2. **MERGE 模式错误**: `f93ea62` — MERGE 不加 label 导致节点重复
3. **source field 缺失**: 同上 hotfix — 模糊查找实体时遗漏 source 字段
4. **ChromaDB 返回值格式**: `1b2d288` — ChromaDB 返回值类型与预期不符
5. **SSE done 事件时序**: `beefb06` — trace SQLite 持久化 + SSE done event timing 修复
6. **LLM 接口切换**: `1feeed1` — 从原始 API 切到智谱 OpenAI 兼容接口
7. **qwen3.5:9b 参数调优**: `c533c73` — batch_size/timeout/num_predict 需要针对模型调优
8. **CSS selector 着色问题**: `06d41db` — 图可视化中的搜索高亮/多选筛选修复

---

*素材收集完成时间: 2026-05-20*
*项目最新 commit: 75632ac feat(phase6.5): Docker containerization*
