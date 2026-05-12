# LayerKG — 本体驱动的可更新知识图谱引擎

<p align="center">
  <strong>用自然语言对话你的代码库</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.13+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/tests-825%20passed-green.svg" alt="Tests">
  <img src="https://img.shields.io/badge/code-10.8K%20LOC-orange.svg" alt="LOC">
  <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License">
</p>

---

## 🎯 项目简介

LayerKG 是一个**本体驱动的智能开发运维平台**，能从源码和文档自动构建知识图谱，通过自然语言对话查询代码架构、依赖关系和变更影响分析。

```
"改了 merge_node() 会影响谁？"  →  BFS 影响传播分析
"项目有哪些设计模式？"           →  概念对齐 + 语义搜索
"最复杂的模块是哪个？"           →  Agent 多步推理
```

### 核心特性

- 🔍 **自动构建** — Tree-sitter AST 解析 + LLM 语义提取，一键构建代码知识图谱
- 🔄 **增量更新** — git diff → 变更检测 → 双向 BFS 影响传播 → 选择性重生成
- 🧠 **概念对齐** — 四步对齐器（精确→别名→向量→图结构），解决术语漂移
- 🤖 **Agent 编排** — LangGraph ReAct Agent，91.4% 工具选择准确率
- 🌐 **Web UI** — Vue 3 + Cytoscape 图谱可视化 + SSE 流式对话
- 📡 **MCP Server** — 8 个工具暴露给外部 Agent，标准协议接入

---

## 🏗️ 架构全景

```
┌────────────────────────────────────────────────────┐
│              用户交互层                               │
│   Web UI (Vue3) / CLI (Click) / MCP Client         │
├────────────────────────────────────────────────────┤
│           Agent 编排层 (LangGraph ReAct)             │
│   Router → QueryNode / QueryEdge / ImpactAnalysis  │
│              ↕ MCP 工具调用                          │
├────────────────────────────────────────────────────┤
│            LayerKG 知识引擎                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐         │
│  │ 提取引擎  │  │ 增量引擎  │  │ 查询引擎  │         │
│  │Tree-sitter│  │ git diff  │  │ 图遍历    │         │
│  │ LLM 语义  │  │BFS 影响传播│  │ 语义搜索  │         │
│  │ 概念对齐器 │  │ 选择重生成 │  │ 混合检索  │         │
│  └──────────┘  └──────────┘  └──────────┘         │
│  统一 Schema: 6 实体 + 11 关系                      │
├────────────────────────────────────────────────────┤
│  Neo4j (图存储)  │  ChromaDB (向量索引)  │  SQLite   │
└────────────────────────────────────────────────────┘
```

---

## 📦 技术栈

| 层级 | 技术 | 用途 |
|------|------|------|
| **后端** | Python 3.13 + FastAPI | API 服务 + SSE 流式 |
| **Agent** | LangGraph + DeepSeek | ReAct 多工具编排 |
| **AST** | Tree-sitter | 代码结构解析 |
| **图存储** | Neo4j 5.x | 实体关系持久化 |
| **向量** | ChromaDB | 语义嵌入索引 |
| **MCP** | FastMCP | 工具协议暴露 |
| **前端** | Vue 3 + TypeScript + Vite | Web UI |
| **可视化** | Cytoscape.js + Mermaid | 图谱 + 流程图 |
| **CLI** | Click | 命令行工具 |
| **质量** | pytest + ruff | 测试 + 代码规范 |

---

## 📂 项目结构

```
ontology-driven-agent/
├── src/layerkg/
│   ├── schema.py              # 6实体 + 11关系 dataclass
│   ├── config.py              # 配置管理 + .env 自动加载
│   ├── graph_store.py         # GraphStore 抽象接口
│   ├── neo4j_store.py         # Neo4j 图存储实现
│   ├── chroma_store.py        # ChromaDB 向量存储
│   ├── builder.py             # 5阶段构建流水线
│   ├── incremental_updater.py # 增量更新引擎
│   ├── change_detector.py     # Git 变更检测
│   ├── impact_propagator.py   # 双向 BFS 影响传播
│   ├── aligner.py             # 四步概念对齐器
│   ├── module_clustering.py   # Label Propagation 模块聚类
│   ├── cli.py                 # Click CLI 入口
│   ├── mcp_server.py          # FastMCP Server (8 工具)
│   ├── parser/
│   │   ├── python_parser.py   # Tree-sitter Python 解析器
│   │   └── doc_parser.py      # Markdown/RST 文档解析
│   ├── extractor/
│   │   ├── relation.py        # 结构关系提取 (calls/extends/...)
│   │   └── semantic.py        # LLM 语义关系提取
│   ├── agent/
│   │   ├── graph.py           # LangGraph ReAct Agent
│   │   ├── tools.py           # Agent 工具定义
│   │   ├── prompt.py          # Agent 系统提示词
│   │   └── trace.py           # TraceCollector 可观测性
│   └── web/
│       ├── app.py             # FastAPI 应用
│       └── router/
│           ├── chat.py        # SSE 流式对话 API
│           ├── graph.py       # 图谱查询 API
│           └── trace.py       # 追踪查询 API
├── frontend/
│   └── src/
│       ├── views/
│       │   ├── ChatView.vue       # 对话界面
│       │   ├── GraphView.vue      # Cytoscape 图谱
│       │   ├── TracesView.vue     # 追踪列表
│       │   └── TraceDetailView.vue # 追踪详情 + Mermaid
│       ├── components/            # UI 组件
│       └── stores/                # Pinia 状态管理
├── tests/                    # 825 tests
├── docs/plans/               # 实施方案文档
└── pyproject.toml
```

---

## 🚀 快速开始

### 环境依赖

- Python 3.13+
- Node.js 18+ (前端构建)
- Neo4j 5.x
- Ollama (可选，用于本地语义提取)

### 安装

```bash
# 克隆项目
git clone https://gitee.com/sinxyql/ontology-driven-agent.git
cd ontology-driven-agent

# 安装 Python 依赖
uv sync

# 安装前端依赖
cd frontend && npm install && cd ..

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入 Neo4j / LLM 配置
```

### 配置 (.env)

```bash
# Neo4j
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password

# LLM (OpenAI 兼容接口)
LLM_MODEL=deepseek-chat
DEEPSEEK_API_KEY=your_key
DEEPSEEK_BASE_URL=https://api.deepseek.com

# Ollama (可选)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_EMBED_MODEL=qwen2.5-coder:0.5b
```

### 使用

```bash
# 全量构建知识图谱
uv run layerkg build ./your-repo --clear

# 语义搜索
uv run layerkg query "处理用户认证的函数"

# 自然语言对话
uv run layerkg ask "改了 builder.py 会影响谁？"

# 增量更新
uv run layerkg update --since HEAD~1

# 启动 MCP Server
uv run layerkg serve

# 启动 Web UI
uv run layerkg web
# 前端开发模式（另起终端）
cd frontend && npm run dev
```

---

## 🧩 LayerKG Schema

### 6 实体

| 实体 | 类型 | 说明 |
|------|------|------|
| `CodeEntity` | function / class / interface / module / file | 代码结构实体 |
| `ConceptEntity` | business_concept / design_pattern / api_contract / data_model / process | 业务与设计概念 |
| `DocEntity` | readme / module_doc / api_doc / wiki / architecture_doc | 文档实体 |
| `ResourceEntity` | image / diagram / pdf / config / schema_file | 资源实体 |
| `ModuleEntity` | 功能模块 | 聚类结果 |
| `ChangeSetEntity` | 变更集 | Git commit 关联 |

### 11 关系

| 类别 | 关系 | 说明 |
|------|------|------|
| 结构 (AST) | `calls` / `extends` / `implements` / `imports` / `contains` | 代码静态结构 |
| 语义 (LLM) | `semantic_impact` / `describes` / `illustrates` / `derived_from` | 语义关联 |
| 变更 | `changed_in` / `affects` | 变更影响追踪 |

---

## 🤖 Agent 能力

Agent 基于 LangGraph ReAct 模式，自动选择工具完成复杂查询：

| 工具 | 能力 |
|------|------|
| `semantic_search` | 语义搜索代码/文档实体 |
| `query_node` | 按名称/类型查询节点 |
| `query_edge` | 查询关系和调用链 |
| `impact_analysis` | BFS 影响传播分析 |
| `get_context` | 获取实体 360° 上下文 |
| `list_concepts` | 列出业务概念 |
| `get_module_tree` | 模块层次结构 |
| `export_graph` | 导出图谱数据 |

**评估结果：** 35 道自建测试题，工具选择准确率 **91.4%**

---

## 📊 项目数据

| 指标 | 数值 |
|------|------|
| 后端代码 | 8,450 行 Python |
| 前端代码 | 2,361 行 TypeScript/Vue |
| 测试用例 | 825 passed |
| Git 提交 | 55 commits |
| 知识图谱 | 382 节点 / 1,131 边（自身代码库） |
| 开发周期 | Phase 0-4, 12 天 |

---

## 📝 开发状态

- [x] **Phase 0** — LayerKG 骨架 (Schema + Neo4j + ChromaDB + Parser + CLI)
- [x] **Phase 1** — 语义提取 + 概念对齐 + 增量引擎 + MCP Server
- [x] **Phase 2** — 全量构建 Pipeline (9 天实施 + 数据质量)
- [x] **Phase 3** — LangGraph Agent 编排 (ReAct + 评估 91.4%)
- [x] **Phase 4** — Web UI + 图谱可视化 + 端到端联调
- [ ] **Phase 5** — Butler 永续 Agent + 三层技能进化闭环
- [ ] **Phase 6** — 性能优化 + Docker 部署 + Demo 打磨

---

## 📜 License

MIT
