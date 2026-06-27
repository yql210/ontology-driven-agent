# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **For Hermes:** 使用 claude-code skill 的 print mode (`-p`) 执行任务。每个任务 2-5 分钟粒度。强制 TDD。详细规范见 `.claude/rules/` 目录。

## 项目简介

LayerKG — 基于本体驱动的可更新知识图谱引擎。从源代码 + 文档自动构建知识图谱（Neo4j + ChromaDB），支持自然语言查询、变更影响分析、增量更新。上层 LangGraph ReAct Agent 编排查询与操作。

技术栈：Python 3.13+ · uv · tree-sitter（Python+Java）· Neo4j 5.x · ChromaDB · LangGraph · Click · ruff · pyright。

## 精确命令

```bash
# 包管理
uv add <package>              # 添加依赖（运行时）
uv add --dev <package>        # 添加开发依赖
uv sync                       # 同步环境

# 测试（markers: unit / integration / slow）
uv run pytest tests/ -v                              # 全量
uv run pytest tests/unit/ -v                         # 仅单元测试
uv run pytest -m "not integration" -v                # 跳过集成测试（无需 Neo4j）
uv run pytest tests/unit/test_schema.py::test_x -v   # 单个测试
uv run pytest tests/ --cov=layerkg --cov-report=term-missing  # 覆盖率

# 静态检查 / 格式化 / 类型检查（提交前必须通过）
uv run ruff check src/ tests/        # 静态检查
uv run ruff check --fix src/ tests/  # 自动修复
uv run ruff format src/ tests/       # 自动格式化
uv run pyright src/                  # 类型检查

# CLI（入口 layerkg = layerkg.api.cli:main；配置从 .env / 环境变量读取）
uv run layerkg build ./repo [--skip-semantic] [--skip-clustering] [--clear] [--verbose-build]
uv run layerkg query "text" [-t function|class|...] [-n 10]
uv run layerkg update ./repo [--since HEAD~1] [--dry-run] [--full-scan]   # 增量更新
uv run layerkg migrate [--target <ver>]                                    # schema 迁移 / 回滚
uv run layerkg ask "merge_node 被谁调用" | ask -i                          # LangGraph Agent 问答
uv run layerkg serve [--transport stdio|http] [--port 8000]                # MCP Server
uv run layerkg web [--host 0.0.0.0] [--port 8000] [--reload]               # FastAPI Web API
uv run layerkg info | version
uv run layerkg butler {serve|update|build|status}                          # 事件驱动知识管理引擎

# Git
git add -A && git commit -m "type: description"
```

## 架构（V3.4 四层架构 — 需要跨多个文件理解的全局图景）

```
Intent（意图层）    Agent 识别意图 → express_intent 工具路由到 Action
Control（控制层）   ActionExecutor · Submission Criteria · 审批 · SAGA · TransactionManager
Capability（能力层）Function 注册表（通用+领域）· FunctionRunner（重试/熔断/并发）· Connector
Semantic（语义层）  Schema（6 实体 11 关系）· GraphStore（Neo4j + ChromaDB）· 本体约束
```

**关键约束：每层只依赖下一层，不跨层不反向。** Action 只引用 Function 名；Function 通过 `graph_store` 操作语义层、通过 `Connector` 访问外部系统；Connector 只搬运数据不含业务逻辑。

### 意图 → 执行 的完整链路
1. Agent 收到自然语言 → prompt 中的 `trigger_hint` 列表匹配 `intent_type`（prompt 由 `intent_router.build_intent_prompt()` 从 `pipeline/ontology_actions.yaml` 自动生成）。
2. Agent 调用工具 `express_intent(intent_type, target, params)`（`agent/tools.py`）。
3. `ActionExecutor.execute()`（`execution/action_executor.py`）：查 `intent_map` → `_resolve_entity` → `_check_criteria`（Submission Criteria）→ 通过 `FunctionRunner` 同步执行对应 Function。
4. Function 经 `ActionContext` 注入 `graph_store` + `function_runner`，可链式调用其他 Function；写操作走 `TransactionManager` / SAGA 保证原子性与补偿。

### 各层落地位置（重构后目录结构）
| 层 | 目录 | 关键文件 |
|----|------|---------|
| Domain | `domain/` | `schema.py`、`exceptions.py`、`provenance.py` |
| Store | `store/` | `graph_store.py`（抽象）、`neo4j_store.py`、`chroma_store.py`、`schema_version.py`、`migrations/` |
| Parsing | `parsing/` | `parser/`（python/java/doc）、`extractor/`（relation、semantic） |
| Pipeline | `pipeline/` | `builder.py`、`builder_utils.py`、`semantic_linker.py`、`incremental_updater.py`、`change_detector.py`、`impact_propagator.py`、`aligner.py`、`module_clustering.py`、`ontology_actions.yaml` |
| Execution | `execution/` | `action_executor.py`、`action_types.py`、`intent_router.py`、`function_runner.py`、`circuit_breaker.py`、`execution_policy.py`、`saga.py`、`transaction_manager.py`、`functions/`、`connectors/` |
| Agent | `agent/` | `graph.py`、`tools.py`、`prompt.py`、`_helpers.py` |
| Butler | `butler/` | `engine.py`、`event_bus.py`、`scheduler.py`、`handlers/` |
| API | `api/` | `cli.py`、`mcp_server.py`、`web/` |

> 架构约束详见 `.claude/rules/architecture.md`（根目录文件上限 5 个、单文件行数上限 800、分层单向依赖）。

## 知识图谱构建流水线（`pipeline/builder.py::LayerKGBuilder.build`）

多阶段管线，**只有前两阶段是关键路径**（失败立即 `aborted=True`），后续阶段（语义/聚类/向量）可降级跳过：

| 阶段 | 作用 | 失败行为 |
|------|------|---------|
| Stage 1 Parse | tree-sitter 扫描解析（`parsing/parser/`：Python+Java+doc）+ 结构关系提取 | 关键 |
| Stage 2 Structural Write | 结构实体/关系 MERGE 写入 Neo4j | 关键（`RuntimeError`） |
| Stage 2.5 Doc-Code Link | 文档→代码 `DESCRIBES` 关联 | 关键 |
| Stage 3 Semantic | LLM 语义提取（`parsing/extractor/semantic.py`）+ 概念对齐（`pipeline/aligner.py` 四步：精确→别名→向量→图结构）→ 写 ConceptEntity | 可降级 |
| Stage 4 Clustering | 模块聚类（`pipeline/module_clustering.py`）→ ModuleEntity | 可降级 |
| Stage 5 Vector Index | 实体向量写入 ChromaDB（`store/chroma_store.py`，Ollama embedding） | 可降级 |

增量更新走 `pipeline/incremental_updater.py`（基于 `pipeline/change_detector.py` git diff + `pipeline/impact_propagator.py` 双向 BFS 影响传播）。Butler 引擎（`butler/`：EventBus + Handler + GitWatcher）把上述能力包装成事件驱动的常驻服务。

## 测试结构
```
tests/
├── conftest.py             # autouse fixture 在每个测试后重置 agent LLM 全局单例（_reset_llm）
├── unit/                   # 无外部依赖（默认开发跑这一层）
│   ├── agent/              # agent/ 的测试
│   ├── butler/             # butler/ 的测试
│   ├── execution/          # execution/ 的测试
│   ├── pipeline/           # pipeline/ 的测试
│   ├── web/                # api/web/ 的测试
│   └── *.py                # domain/store/parsing 等小模块测试
├── integration/            # 需要真实 Neo4j（@pytest.mark.integration）
└── evaluation/             # 评测集 + run_eval.py
```
- 优先测真实行为，只对 LLM/外部服务 mock；mock 放测试函数内，不放 conftest。
- 测试子目录与 `src/layerkg/` 子包对应，详见 `.claude/rules/architecture.md`。
- 详细 TDD / 命名 / AAA / 覆盖率规范见 `.claude/rules/testing.md`。

## LayerKG Schema 速查
**6 实体**（Neo4j Label = dataclass 名）：`CodeEntity`（function/class/interface/module/file/enum/record/field）、`ConceptEntity`（business_concept/design_pattern/api_contract/data_model/process）、`DocEntity`、`ResourceEntity`、`ModuleEntity`、`ChangeSetEntity`。

**11 关系**（Neo4j Type = UPPER_SNAKE）：
- 结构（AST）：`CALLS` `EXTENDS` `IMPLEMENTS` `IMPORTS` `CONTAINS`
- 语义（LLM）：`SEMANTIC_IMPACT` `DESCRIBES` `ILLUSTRATES` `DERIVED_FROM`
- 变更：`CHANGED_IN` `AFFECTS`

属性名 camelCase（`entityType`、`filePath`）；Cypher 必须**参数化**，禁止字符串拼接。约束规范见 `.claude/rules/neo4j.md`。

## 配置 & 外部服务（`.env`，参考 `.env.example`）
- **Neo4j**：`LAYERKG_NEO4J_URI` / `_USER` / `_PASSWORD`
- **Ollama**：`LAYERKG_OLLAMA_URL`、`LAYERKG_EMBEDDING_MODEL`（默认 qwen2.5-coder:0.5b）、`LAYERKG_LLM_MODEL`
- **语义提取 LLM**：`LAYERKG_SEMANTIC_LLM_PROVIDER`（ollama|openai）、`LAYERKG_SEMANTIC_API_KEY`、`LAYERKG_SEMANTIC_BASE_URL`
- **Agent LLM**：`LAYERKG_AGENT_LLM_PROVIDER`（默认 zhipu）、`LAYERKG_AGENT_LLM_MODEL`、`LAYERKG_AGENT_API_KEY`、`LAYERKG_AGENT_BASE_URL`
- **ChromaDB**：`LAYERKG_CHROMA_DIR`（默认 `.chroma`）
- **构建**：`LAYERKG_BUILD_INCLUDE_DOCS`、`LAYERKG_BUILD_DOC_EXTENSIONS`、`LAYERKG_BUILD_SKIP_DIRS` 等
- Docker：`docker compose up -d` 起 Neo4j + ChromaDB + LayerKG

## 前端（`frontend/`，独立工程）
Vue 3 + Vite + TypeScript。可视化图谱（cytoscape）、对话（SSE 经 `@microsoft/fetch-event-source` 连 Web API `/chat/stream`）、流程图（mermaid）、Markdown（marked）。`npm run dev` / `npm run build`。

## 编码规范（详见 `.claude/rules/`，此处仅列高频项）
- Python：`from __future__ import annotations` 头部；类型注解必须（`X | None`、`list[X]`、`Path`）；f-string；行宽 120；`@dataclass` + `__post_init__` 校验；自定义异常继承 `LayerKGError`；用 `logging` 不用 `print`。
- 提交前：`ruff check` + `ruff format` + `pyright` 全部通过。

## 设计文档 & 历史计划
- `docs/design/DESIGN_V34.md` — **当前架构**（四层）。`DESIGN_V3.md` / `DESIGN_V33.md` 为历史演进。
- `docs/plans/`（75 份按天规划）、`.hermes/plans/`（Hermes day0-day4）— 历史实施计划，仅供回溯。
