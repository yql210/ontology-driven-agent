---
title: OntoAgent 快速上手指南
date: 2026-06-28
lastmod: 2026-06-28
---

# OntoAgent 快速上手指南

OntoAgent 是基于本体驱动的可更新知识图谱引擎，能够从源代码和文档自动构建知识图谱（Neo4j + ChromaDB），支持自然语言查询、变更影响分析和增量更新。上层 LangGraph ReAct Agent 编排查询与操作。

本指南将帮助你在 5 分钟内完成从安装到构建知识图谱的全过程。

## 1. 系统要求

| 依赖 | 最低版本 | 说明 |
|------|----------|------|
| Python | 3.13+ | 使用了 3.13 新语法特性 |
| uv | 0.11+ | 包管理器和虚拟环境工具，替代 pip |
| Neo4j | 5.x | 图数据库，存储实体和关系 |
| Ollama | 最新（可选） | 本地 LLM 服务，用于语义提取和嵌入；不安装可通过 `--skip-semantic` 跳过 |
| Git | 2.x | 增量更新依赖 Git diff |

### 1.1 硬件建议

| 资源 | 最低 | 推荐 |
|------|------|------|
| CPU | 2 核 | 4 核+ |
| 内存 | 4 GB | 8 GB+ |
| 磁盘 | 2 GB | 10 GB+（含 Neo4j 数据） |

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

# 同步项目环境（含所有运行时依赖）
uv sync
```

如需参与开发，可加装开发依赖：

```bash
uv sync --dev
```

### 2.3 验证安装

```bash
uv run ontoagent version
```

输出示例：

```
OntoAgent v0.2.0
Python: 3.13.x
Neo4j URI: bolt://...
```

## 3. 配置

### 3.1 创建 `.env` 文件

在项目根目录创建 `.env` 文件（可参考 `.env.example`）：

```bash
# ---- Neo4j ----
ONTOAGENT_NEO4J_URI=bolt://localhost:7687
ONTOAGENT_NEO4J_USER=neo4j
ONTOAGENT_NEO4J_PASSWORD=your_password

# ---- Ollama（本地 LLM / Embedding）----
ONTOAGENT_OLLAMA_URL=http://localhost:11434
ONTOAGENT_EMBEDDING_MODEL=qwen2.5-coder:0.5b
ONTOAGENT_LLM_MODEL=qwen3.5:9b

# ---- 语义提取 LLM（可选，替代 Ollama）----
# 提供商：ollama（默认）| openai
# ONTOAGENT_SEMANTIC_LLM_PROVIDER=openai
# ONTOAGENT_SEMANTIC_API_KEY=sk-xxx
# ONTOAGENT_SEMANTIC_BASE_URL=https://api.openai.com/v1

# ---- ChromaDB ----
ONTOAGENT_CHROMA_DIR=.chroma

# ---- Agent LLM（Web 对话 / ask 命令）----
ONTOAGENT_AGENT_LLM_PROVIDER=zhipu
ONTOAGENT_AGENT_LLM_MODEL=glm-4-flash
ONTOAGENT_AGENT_API_KEY=your_api_key
ONTOAGENT_AGENT_BASE_URL=https://open.bigmodel.cn/api/anthropic
```

> **重要**：所有环境变量必须以 `ONTOAGENT_` 为前缀，不可使用旧版 `LAYERKG_` 前缀。

### 3.2 配置项一览

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `ONTOAGENT_NEO4J_URI` | `bolt://localhost:7687` | Neo4j 连接地址 |
| `ONTOAGENT_NEO4J_USER` | `neo4j` | Neo4j 用户名 |
| `ONTOAGENT_NEO4J_PASSWORD` | （空） | Neo4j 密码 |
| `ONTOAGENT_CHROMA_DIR` | `.chroma` | ChromaDB 持久化目录 |
| `ONTOAGENT_OLLAMA_URL` | `http://localhost:11434` | Ollama 服务地址 |
| `ONTOAGENT_EMBEDDING_MODEL` | `qwen2.5-coder:0.5b` | 嵌入模型 |
| `ONTOAGENT_LLM_MODEL` | `qwen3.5:9b` | 语义提取 LLM 模型 |
| `ONTOAGENT_SEMANTIC_LLM_PROVIDER` | `ollama` | 语义提取 LLM 提供商（ollama / openai） |
| `ONTOAGENT_SEMANTIC_API_KEY` | （空） | 语义提取 LLM API Key |
| `ONTOAGENT_SEMANTIC_BASE_URL` | （空） | 语义提取 LLM API 地址 |
| `ONTOAGENT_AGENT_LLM_PROVIDER` | `zhipu` | Agent LLM 提供商 |
| `ONTOAGENT_AGENT_LLM_MODEL` | `glm-4-flash` | Agent LLM 模型 |
| `ONTOAGENT_AGENT_API_KEY` | （空） | Agent API 密钥 |
| `ONTOAGENT_AGENT_BASE_URL` | `https://open.bigmodel.cn/api/anthropic` | Agent API 地址 |
| `ONTOAGENT_BUILD_INCLUDE_DOCS` | `true` | 是否扫描文档文件 |
| `ONTOAGENT_BUILD_DOC_EXTENSIONS` | `.md,.rst` | 文档文件扩展名 |
| `ONTOAGENT_BUILD_SKIP_DIRS` | 见下方 | 跳过的目录（逗号分隔） |

默认跳过目录：`__pycache__`, `.git`, `.mypy_cache`, `.ruff_cache`, `.pytest_cache`, `node_modules`, `.venv`, `venv`, `site`, `.tox`, `dist`, `build`, `*.egg-info`

### 3.3 验证配置

```bash
uv run ontoagent info
```

输出示例：

```
Configuration:
  Neo4j: bolt://localhost:7687
  Ollama: http://localhost:11434
  Embedding Model: qwen2.5-coder:0.5b
  ChromaDB: .chroma
Entities in ChromaDB: 0
```

## 4. 5 分钟快速构建

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
# 构建指定仓库的知识图谱
uv run ontoagent build ./repo

# 清空数据库后重建
uv run ontoagent build ./repo --clear

# 跳过语义提取（无需 Ollama 时）
uv run ontoagent build ./repo --skip-semantic

# 跳过模块聚类
uv run ontoagent build ./repo --skip-clustering

# 同时跳过语义和聚类（仅做结构解析 + 写入，速度最快）
uv run ontoagent build ./repo --clear --skip-semantic --skip-clustering
```

> **提示**：不带 `--clear` 时，构建会将新实体和关系追加（MERGE）到已有图谱中。

构建输出示例：

```
Build complete: 42 files scanned, 9609 entities created, 7429 relations created
```

### 4.3 知识图谱查询

```bash
# 搜索与 "merge_node" 相关的代码实体
uv run ontoagent query "merge_node"

# 按类型过滤
uv run ontoagent query "解析器" -t class

# 限制返回数量
uv run ontoagent query "数据库连接" -n 5
```

### 4.4 增量更新

```bash
# 基于 Git diff 增量更新（默认对比 HEAD~1）
uv run ontoagent update ./repo

# 指定对比基准
uv run ontoagent update ./repo --since HEAD~3

# 只检测不执行
uv run ontoagent update ./repo --dry-run

# 全量扫描（不依赖 Git）
uv run ontoagent update ./repo --full-scan
```

### 4.5 向 Agent 提问

```bash
# 单次提问
uv run ontoagent ask "merge_node 被谁调用"

# 交互模式（进入对话界面，quit / exit 退出）
uv run ontoagent ask -i
```

Agent LLM 默认使用 zhipu provider，模型为 `glm-4-flash`。需在 `.env` 中配置 `ONTOAGENT_AGENT_API_KEY`。

### 4.6 启动 MCP Server

```bash
# stdio 模式（供 Claude Code / Cursor 等集成）
uv run ontoagent serve

# HTTP 模式
uv run ontoagent serve --transport http --port 8000
```

### 4.7 信息查看

```bash
uv run ontoagent info      # 查看配置摘要和数据库状态
uv run ontoagent version   # 查看版本信息
```

## 5. 约束配置简介

OntoAgent 的本体约束系统确保知识图谱的操作符合业务规则和安全策略。配置文件位于 `src/ontoagent/pipeline/constraints.yaml` 和 `src/ontoagent/config/constraint_overrides.yaml`。

### 5.1 遍历约束 (`constraints.yaml`)

定义基于图遍历的属性聚合约束，例如：

```yaml
traversal_constraints:
  data_sensitivity:
    name: data_sensitivity
    source_label: "CodeEntity"
    relation_chain: ["PROCESSES_DATA"]
    target_label: "DataAsset"
    collect_property: "sensitivity"
    aggregation: "max"
```

该约束检查 `CodeEntity` 通过 `PROCESSES_DATA` 关系可达的 `DataAsset` 的 `sensitivity` 属性，取最大值作为该代码实体的数据敏感级别。

`propagation_rules` 定义属性传播规则：

```yaml
propagation_rules:
  upstream_risk:
    name: upstream_risk
    along: ["CALLS"]
    direction: "backward"
    max_depth: 5
    collect_property: "entryCategory"
    aggregation: "exists"
```

### 5.2 约束覆盖 (`constraint_overrides.yaml`)

当自动推导的约束不满足实际需求时，可通过覆盖文件调整，支持三种覆盖类型：

- **patch**：局部修改已有约束（调整约束级别、移除/添加特定值的约束）
- **allow_all**：单点白名单（对特定实体完全豁免约束检查，可设过期时间）
- **add_constraint**：追加额外约束（定义新的遍历路径和聚合规则）

覆盖优先级高于本体自动推导。

## 6. 审批配置简介

审批系统用于在执行敏感操作前进行安全审查，配置文件位于 `src/ontoagent/config/`。

### 6.1 审批策略 (`approval_policy.yaml`)

定义审批流程的核心行为：

```yaml
policies:
  - guard_result      # Guard Pipeline 结果 → 审批
  - action_approval   # ActionConfig.requires_approval → 审批
  - function_danger   # Function.danger_level → 审批

guard_result:
  on_block: require_approval     # BLOCK 级别 → 必须审批
  on_warn: require_approval      # WARN 级别 → 需要审批

token:
  ttl: 600           # 审批令牌有效期（秒）
  max_pending: 10    # 同时最多待审批数
```

三种策略按顺序执行，任一策略触发审批即进入审批流程。

### 6.2 函数危险级别 (`function_danger_levels.yaml`)

为各 Function 分配危险等级：

```yaml
default: read   # 未显式列出的函数默认 read

functions:
  query_entity: read            # 纯查询，自动放行
  trace_business_impact: read_sensitive   # 查询敏感数据，需审批
  update_entity: write          # 写操作，需审批
  create_entity: admin          # 系统级操作，需审批
```

危险级别含义：

| 级别 | 含义 | 默认审批行为 |
|------|------|-------------|
| `read` | 纯查询 | 自动批准 |
| `read_sensitive` | 查询敏感数据 | 需要审批 |
| `write` | 数据修改 | 需要审批 |
| `admin` | 系统级操作 | 需要审批 |

### 6.3 工具网关 (`tool_gateway.yaml`)

拦截 Agent 对 Neo4j 的直接写操作：

```yaml
enabled: true

blocked_keywords:
  - SET
  - DELETE
  - REMOVE
  - CREATE
  - MERGE
  - DROP
  - "DETACH DELETE"
  - FOREACH
  - "CALL apoc"
```

该网关确保 Agent 必须通过正式 Function 执行写操作，不能直接发送 Cypher 修改语句。

## 7. Web 界面

OntoAgent 提供基于 Vue 3 + Vite + TypeScript 的 Web 前端。

### 7.1 启动后端

```bash
uv run ontoagent web

# 自定义主机和端口
uv run ontoagent web --host 0.0.0.0 --port 8000
```

### 7.2 启动前端

```bash
cd frontend
npm install
npm run dev
```

访问 `http://localhost:5173` 即可使用 Web 界面。

### 7.3 Web 界面功能

- **Chat** — 对话式代码问答，支持 SSE 流式输出
- **Graph** — 知识图谱可视化（Cytoscape.js 图布局 + 居中扩展浏览）
- **Traces** — Agent 推理轨迹查看（Mermaid 流程图渲染）

## 8. Schema 速查

### 8.1 10 种实体

| 实体 | Neo4j 标签 | entity_type 值 | 说明 |
|------|-----------|---------------|------|
| CodeEntity | `CodeEntity` | `function`, `class`, `interface`, `module`, `file`, `enum`, `record`, `field` | 代码结构实体 |
| ConceptEntity | `ConceptEntity` | `business_concept`, `design_pattern`, `api_contract`, `data_model`, `process` | 语义概念实体 |
| DocEntity | `DocEntity` | `readme`, `module_doc`, `api_doc`, `comment`, `wiki`, `architecture_doc` | 文档实体 |
| ResourceEntity | `ResourceEntity` | `image`, `diagram`, `pdf`, `config`, `schema_file`, `log` | 资源文件实体 |
| ModuleEntity | `ModuleEntity` | （无 entity_type 字段） | 功能模块（聚类结果） |
| ChangeSetEntity | `ChangeSetEntity` | （无 entity_type 字段） | Git 变更集 |
| DataAsset | `DataAsset` | `pii`, `financial`, `operational`, `credentials` | 数据资产（敏感/合规） |
| ComplianceItem | `ComplianceItem` | `critical`, `high`, `medium`, `low` | 合规要求 |
| BusinessCapability | `BusinessCapability` | — | 业务能力 |
| AgentAction | `AgentAction` | — | Agent 操作记录 |

### 8.2 21 种关系

| Neo4j 关系类型 | 分类 | 说明 |
|---------------|------|------|
| `CALLS` | 结构 | 函数/方法调用 |
| `EXTENDS` | 结构 | 类继承 |
| `IMPLEMENTS` | 结构 | 接口实现 |
| `IMPORTS` | 结构 | 模块导入 |
| `CONTAINS` | 结构 | 包含关系（模块→子模块、文件→函数等） |
| `SEMANTIC_IMPACT` | 语义 | 语义影响（概念→代码） |
| `DESCRIBES` | 语义 | 文档描述代码 |
| `ILLUSTRATES` | 语义 | 图示说明关系 |
| `DERIVED_FROM` | 语义 | 概念派生来源 |
| `CHANGED_IN` | 变更 | 实体属于某变更集 |
| `AFFECTS` | 变更 | 变更影响传播 |
| `OWNS` | 治理 | 团队/角色拥有实体 |
| `PROTECTS` | 治理 | 合规/安全策略保护实体 |
| `REQUIRES` | 依赖 | 实体依赖关系 |
| `VALIDATES` | 治理 | 合规要求验证实体 |
| `CONFORMS_TO` | 治理 | 实体遵守合规要求 |
| `SUPPORTS` | 业务 | 代码支持业务能力 |
| `TRIGGERS` | 运行时 | 事件/操作触发 |
| `CONSTRAINS` | 治理 | 策略约束实体 |
| `MONITORS` | 运行时 | 监控关系 |
| `ORCHESTRATES` | 流程 | 流程编排关系 |

### 8.3 属性说明

实体属性采用 camelCase 命名，核心属性包括：

- `name`：实体名称（必填，非空）
- `id`：UUID v4 唯一标识符（自动生成）
- `entity_type`：实体子类型
- `file_path`：源文件路径
- `start_line` / `end_line`：代码位置
- `created_at`：ISO 8601 创建时间戳（自动生成）

## 9. 构建流水线概览

全量构建执行 5 阶段流水线：

```
Stage 1: Parse        → tree-sitter AST 解析代码 + 文档，提取实体和结构关系
Stage 2: Write        → 写入 Neo4j（实体节点 + 结构关系 + 外部模块占位）
Stage 2.5: Doc Link   → 文档→代码 describes 关系链接
Stage 3: Semantic     → LLM 语义提取 + 概念对齐去重
Stage 4: Clustering   → 社区检测 → 模块聚类
Stage 5: Vector       → 写入 ChromaDB 向量索引
```

| 阶段 | 是否必须 | 失败影响 |
|------|---------|---------|
| Stage 1–2 | ✅ 必须 | 终止构建 |
| Stage 2.5 | ✅ 必须 | 跳过文档链接（降级） |
| Stage 3 | ❌ 可选（`--skip-semantic`） | 跳过语义关系 |
| Stage 4 | ❌ 可选（`--skip-clustering`） | 跳过模块聚类 |
| Stage 5 | ❌ 可选 | 跳过向量索引（无法语义搜索） |

## 10. 常见问题

**Q: 构建时报 "Neo4j connection refused"**
A: 检查 Neo4j 是否启动，确认 `.env` 中的 `ONTOAGENT_NEO4J_URI` 和密码正确。

**Q: 构建时报 "Ollama connection error"**
A: Ollama 为可选依赖。加 `--skip-semantic` 跳过 Stage 3 即可。或确保 Ollama 运行且模型已拉取。

**Q: ChromaDB 数据存在哪？**
A: 默认在项目根目录的 `.chroma/` 目录。可通过 `ONTOAGENT_CHROMA_DIR` 修改。

**Q: 如何清除所有数据重建？**
A: 使用 `uv run ontoagent build ./repo --clear` 清空 Neo4j 后重建。ChromaDB 数据可手动删除 `.chroma/` 目录。

**Q: Agent 提问报错怎么办？**
A: 检查 Agent LLM 配置（`ONTOAGENT_AGENT_*` 环境变量）。确保 API Key 有效、Base URL 正确。

**Q: CLI 命令找不到？**
A: 请使用 `uv run ontoagent` 前缀执行所有命令，不要直接使用 `ontoagent` 或 `layerkg`。

---

> **项目地址**：[https://gitee.com/sinxyql/ontology-driven-agent](https://gitee.com/sinxyql/ontology-driven-agent)
>
> **源码路径**：`src/ontoagent/`
>
> **CLI 入口**：`ontoagent`（通过 `uv run ontoagent <subcommand>` 调用）
