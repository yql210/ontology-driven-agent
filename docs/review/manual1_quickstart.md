---
title: LayerKG 快速上手指南
date: 2026-05-17T16:38:31+08:00
lastmod: 2026-05-17T16:38:31+08:00
---

# LayerKG 快速上手指南

# LayerKG 快速上手指南

## 1. 系统要求

|依赖|最低版本|说明|
| --------| ----------| -----------------------------------|
|Python|3.13+|使用了 3.13 新语法|
|uv|0.11+|包管理器，替代 pip|
|Neo4j|5.x|图数据库，存储实体和关系|
|Ollama|最新|本地 LLM 服务，用于语义提取和嵌入|
|Git|2.x|增量更新依赖 Git diff|

### 1.1 硬件建议

|资源|最低|推荐|
| ------| ------| ------------------------|
|CPU|2核|4核+|
|内存|4GB|8GB+|
|磁盘|2GB|10GB+（含 Neo4j 数据）|

## 2. 安装

### 2.1 克隆项目

```bash
git clone git@gitee.com:sinxyql/ontology-driven-agent.git
cd ontology-driven-agent
```

### 2.2 安装依赖

```bash
# 安装 uv（如未安装）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 同步项目环境（含所有依赖）
uv sync

# 安装开发依赖（如需开发）
uv sync --dev
```

### 2.3 验证安装

```bash
uv run layerkg version
# 输出: LayerKG v0.1.0
#       Python: 3.13.x
#       Neo4j URI: bolt://...
```

## 3. 配置

### 3.1 创建 `.env` 文件

在项目根目录创建 `.env` 文件：

```bash
# Neo4j 配置
LAYERKG_NEO4J_URI=bolt://localhost:7687
LAYERKG_NEO4J_USER=neo4j
LAYERKG_NEO4J_PASSWORD=your_password

# Ollama 配置
LAYERKG_OLLAMA_URL=http://localhost:11434
LAYERKG_EMBEDDING_MODEL=qwen2.5-coder:0.5b
LAYERKG_LLM_MODEL=qwen3.5:9b

# Agent LLM 配置（Web 对话功能）
LAYERKG_AGENT_LLM_PROVIDER=zhipu
LAYERKG_AGENT_LLM_MODEL=glm-4-flash
LAYERKG_AGENT_API_KEY=your_api_key
LAYERKG_AGENT_BASE_URL=https://open.bigmodel.cn/api/anthropic
```

### 3.2 配置项一览

|环境变量|默认值|说明|
| ----------| --------| ---------------------|
|​`LAYERKG_NEO4J_URI`|​`bolt://localhost:7687`|Neo4j 连接地址|
|​`LAYERKG_NEO4J_USER`|​`neo4j`|Neo4j 用户名|
|​`LAYERKG_NEO4J_PASSWORD`|（空）|Neo4j 密码|
|​`LAYERKG_CHROMA_DIR`|​`.chroma`|ChromaDB 持久化目录|
|​`LAYERKG_OLLAMA_URL`|​`http://localhost:11434`|Ollama 服务地址|
|​`LAYERKG_EMBEDDING_MODEL`|​`qwen2.5-coder:0.5b`|嵌入模型|
|​`LAYERKG_LLM_MODEL`|​`qwen3.5:9b`|语义提取 LLM|
|​`LAYERKG_BUILD_INCLUDE_DOCS`|​`true`|是否扫描文档文件|
|​`LAYERKG_BUILD_DOC_EXTENSIONS`|​`.md,.rst`|文档文件扩展名|
|​`LAYERKG_BUILD_SKIP_DIRS`|见下方|跳过的目录|
|​`LAYERKG_AGENT_LLM_PROVIDER`|​`zhipu`|Agent LLM 提供商|
|​`LAYERKG_AGENT_LLM_MODEL`|​`glm-4-flash`|Agent LLM 模型|
|​`LAYERKG_AGENT_API_KEY`|（空）|Agent API 密钥|
|​`LAYERKG_AGENT_BASE_URL`|​`https://open.bigmodel.cn/api/anthropic`|Agent API 地址|

默认跳过目录：`__pycache__`​, `.git`​, `.mypy_cache`​, `.ruff_cache`​, `.pytest_cache`​, `node_modules`​, `.venv`​, `venv`​, `site`​, `.tox`​, `dist`​, `build`​, `*.egg-info`

### 3.3 验证配置

```bash
uv run layerkg info
# 输出:
# Configuration:
#   Neo4j: bolt://localhost:7687
#   Ollama: http://localhost:11434
#   Model: qwen2.5-coder:0.5b
#   ChromaDB: .chroma
# Entities in ChromaDB: 0
```

## 4. 5 分钟快速开始

### 4.1 启动外部服务

确保 Neo4j 和 Ollama 正在运行：

```bash
# 检查 Neo4j
curl -s http://localhost:7474 | head -1

# 检查 Ollama（需提前拉取模型）
ollama pull qwen2.5-coder:0.5b
ollama pull qwen3.5:9b
```

### 4.2 全量构建知识图谱

```bash
# 构建当前项目的知识图谱
uv run layerkg build .

# 详细输出模式
uv run layerkg build . --verbose-build

# 跳过语义提取（不需要 Ollama 时）
uv run layerkg build . --skip-semantic

# 清空数据库后重建
uv run layerkg build . --clear
```

构建输出示例：

```
Build complete: 42 files scanned, 9609 entities created, 7429 relations created
```

### 4.3 语义搜索

```bash
# 搜索与 "merge_node" 相关的代码实体
uv run layerkg query "merge_node"

# 按类型过滤
uv run layerkg query "解析器" -t class

# 限制返回数量
uv run layerkg query "数据库连接" -n 5
```

### 4.4 增量更新

```bash
# 基于 Git diff 增量更新（默认对比 HEAD~1）
uv run layerkg update .

# 指定对比基准
uv run layerkg update . --since HEAD~3

# 只检测不执行
uv run layerkg update . --dry-run

# 全量扫描（不依赖 Git）
uv run layerkg update . --full-scan
```

### 4.5 向 Agent 提问

```bash
# 单次提问
uv run layerkg ask "merge_node 被谁调用"

# 交互模式
uv run layerkg ask -i
# 进入对话界面，输入问题查询，quit/exit 退出
```

### 4.6 启动 Web 界面

```bash
# 启动后端 API
uv run layerkg web --port 8000

# 启动前端（另一个终端）
cd frontend && npm install && npm run dev
# 访问 http://localhost:5173
```

Web 界面提供：

- **Chat** — 对话式代码问答（支持 SSE 流式输出）
- **Graph** — 知识图谱可视化（Cytoscape.js 图布局 + 居中扩展浏览）
- **Traces** — Agent 推理轨迹查看（Mermaid 流程图渲染）

### 4.7 启动 MCP Server

```bash
# stdio 模式（供 Claude Code / Cursor 等集成）
uv run layerkg serve

# HTTP 模式
uv run layerkg serve --transport http --port 8000
```

### 4.8 启动 Butler Engine

```bash
# 启动后台监控（每 30 秒轮询 Git 变更）
uv run layerkg butler serve --repo . --poll-interval 30

# 手动触发增量更新
uv run layerkg butler update --repo . --since HEAD~1

# 手动触发全量构建
uv run layerkg butler build --repo .

# 查看 Butler 状态
uv run layerkg butler status
```

## 5. 构建流水线详解

全量构建是一个 **5 阶段流水线**：

```
Stage 1: Parse        → AST 解析代码 + 文档，提取实体和结构关系
Stage 2: Write        → 写入 Neo4j（实体节点 + 结构关系 + 外部模块占位）
Stage 2.5: Doc Link   → 文档→代码 describes 关系链接
Stage 3: Semantic     → LLM 语义提取 + 概念对齐去重
Stage 4: Clustering   → 社区检测 → 模块聚类
Stage 5: Vector       → 写入 ChromaDB 向量索引
```

|阶段|是否必须|失败影响|
| -----------| ----------| ------------------------------|
|Stage 1-2|✅ 必须|终止构建|
|Stage 2.5|✅ 必须|跳过文档链接（降级）|
|Stage 3|❌ 可选|跳过语义关系（无 Ollama 时）|
|Stage 4|❌ 可选|跳过模块聚类|
|Stage 5|❌ 可选|跳过向量索引（无法语义搜索）|

## 6. 知识图谱 Schema 速查

### 6 种实体

|实体|Neo4j 标签|entity_type 值|说明|
| -----------------| ------------| ----------------------------------| ----------|
|CodeEntity|​`CodeEntity`|​`function`​, `class`​, `interface`​, `module`​, `file`​, `enum`​, `record`​, `field`|代码结构|
|ConceptEntity|​`ConceptEntity`|​`business_concept`​, `design_pattern`​, `api_contract`​, `data_model`​, `process`|业务概念|
|DocEntity|​`DocEntity`|​`readme`​, `module_doc`​, `api_doc`​, `comment`​, `wiki`​, `architecture_doc`|文档|
|ResourceEntity|​`ResourceEntity`|​`image`​, `diagram`​, `pdf`​, `config`​, `schema_file`​, `log`|资源|
|ModuleEntity|​`ModuleEntity`|（无 entity_type 字段）|功能模块|
|ChangeSetEntity|​`ChangeSetEntity`|（无 entity_type 字段）|变更集|

### 11 种关系

|关系|类型|Neo4j 名称|说明|
| ------| ------| ------------| --------------|
|​`calls`|结构|​`CALLS`|函数调用|
|​`extends`|结构|​`EXTENDS`|类继承|
|​`implements`|结构|​`IMPLEMENTS`|接口实现|
|​`imports`|结构|​`IMPORTS`|模块导入|
|​`contains`|结构|​`CONTAINS`|包含关系|
|​`semantic_impact`|语义|​`SEMANTIC_IMPACT`|语义影响|
|​`describes`|语义|​`DESCRIBES`|文档描述代码|
|​`illustrates`|语义|​`ILLUSTRATES`|图示说明|
|​`derived_from`|语义|​`DERIVED_FROM`|概念来源|
|​`changed_in`|变更|​`CHANGED_IN`|变更归属|
|​`affects`|变更|​`AFFECTS`|变更影响|

## 7. 支持的语言

|语言|扩展名|解析器|实体类型|
| ----------| --------| --------------------| ------------------------------------------------------------------|
|Python|​`.py`|​`PythonParser` (tree-sitter)|module, class, function, enum, field|
|Java|​`.java`|​`JavaParser` (tree-sitter)|file, class, interface, enum, record, method, constructor, field|
|Markdown|​`.md`|​`DocParser` (regex)|readme, module_doc, api_doc, architecture_doc, comment|
|RST|​`.rst`|​`DocParser` (regex)|同上|

## 8. 常见问题

**Q: 构建时报 "Neo4j connection refused"**   
A: 检查 Neo4j 是否启动，确认 `.env`​ 中的 `LAYERKG_NEO4J_URI` 和密码正确。

**Q: 构建时报 "Ollama connection error"**   
A: Ollama 为可选依赖。加 `--skip-semantic` 跳过 Stage 3 即可。或确保 Ollama 运行且模型已拉取。

**Q: ChromaDB 数据存在哪？**   
A: 默认在项目根目录的 `.chroma/`​ 目录。可通过 `LAYERKG_CHROMA_DIR` 修改。

**Q: 如何清除所有数据重建？**   
A: `uv run layerkg build . --clear`​ 会清空 Neo4j 后重建。或手动删除 `.chroma/` 目录。

**Q: Agent 提问报错怎么办？**   
A: 检查 Agent LLM 配置（`LAYERKG_AGENT_*` 环境变量）。确保 API Key 有效、Base URL 正确。
