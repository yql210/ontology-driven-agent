# OntoAgent API 参考手册

## 1. CLI 命令参考

### 1.1 全局选项

```
ontoagent [OPTIONS] COMMAND [ARGS]...

选项:
  -v, --verbose  详细输出（DEBUG 日志级别）
  --help         显示帮助
```

### 1.2 build — 全量构建

```bash
ontoagent build REPO_PATH [OPTIONS]
```

| 选项 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `REPO_PATH` | 路径（必填） | — | 目标仓库目录 |
| `--skip-semantic` | flag | false | 跳过 Stage 3 语义提取 |
| `--skip-clustering` | flag | false | 跳过 Stage 4 模块聚类 |
| `--verbose-build` | flag | false | 逐阶段输出详情 |
| `--clear` | flag | false | 清空数据库后重建 |

**示例**:

```bash
ontoagent build ./my-project
ontoagent build ./my-project --skip-semantic --skip-clustering
ontoagent build ./my-project --clear --verbose-build
```

**输出**: `Build complete: X files scanned, Y entities created, Z relations created`

### 1.3 query — 语义搜索

```bash
ontoagent query TEXT [OPTIONS]
```

| 选项 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `TEXT` | 文本（必填） | — | 搜索查询 |
| `-t, --type` | 文本 | — | 实体类型过滤（如 function/class/module） |
| `-n, --limit` | 整数 | 10 | 返回数量 |

**示例**:

```bash
ontoagent query "数据库连接"
ontoagent query "解析器" -t class -n 5
```

### 1.4 update — 增量更新

```bash
ontoagent update REPO_PATH [OPTIONS]
```

| 选项 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `REPO_PATH` | 路径（必填） | — | 目标仓库目录 |
| `--since` | 文本 | `HEAD~1` | Git 对比基准 |
| `--dry-run` | flag | false | 只检测不执行 |
| `--full-scan` | flag | false | 全量扫描（不依赖 Git diff） |

### 1.5 ask — Agent 对话

```bash
ontoagent ask [QUESTION] [OPTIONS]
```

| 选项 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `QUESTION` | 文本（可选） | — | 单次问题 |
| `-i, --interactive` | flag | false | 交互模式 |

**示例**:

```bash
ontoagent ask "merge_node 被谁调用"
ontoagent ask -i  # 进入交互模式，quit/exit 退出
```

### 1.6 serve — MCP Server

```bash
ontoagent serve [OPTIONS]
```

| 选项 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `--transport` | `stdio`\|`http` | `stdio` | 传输协议 |
| `--port` | 整数 | 8000 | HTTP 模式端口 |

启动后注册 **8 个 MCP 工具**：`semantic_search`、`graph_query`、`impact_analysis`、`get_context`、`list_concepts`、`get_module_tree`、`detect_changes`、`export_graph`。

**stdio 集成示例**（Claude Code / Cursor）:

```json
{
  "mcpServers": {
    "ontoagent": {
      "command": "uv",
      "args": ["run", "ontoagent", "serve"]
    }
  }
}
```

### 1.7 web — Web API Server

```bash
ontoagent web [OPTIONS]
```

| 选项 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `--host` | 文本 | `0.0.0.0` | 监听地址 |
| `--port` | 整数 | 8000 | 监听端口 |
| `--reload` | flag | false | 开发模式热重载 |

启动 FastAPI 服务，提供 REST API + SSE 流式对话 + Trace 查询。前端 `frontend/` 通过 `@microsoft/fetch-event-source` 连接 `/api/chat/stream`。

### 1.8 info — 配置和状态

```bash
ontoagent info
```

显示: Neo4j URI、Ollama URL、嵌入模型、ChromaDB 路径、ChromaDB 实体数。

### 1.9 version — 版本信息

```bash
ontoagent version
```

显示: OntoAgent 版本、Python 版本、Neo4j URI。

### 1.10 migrate — Schema 迁移

```bash
ontoagent migrate [OPTIONS]
```

| 选项 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `--target` | 文本 | — | 目标版本号（用于回滚） |

不指定 `--target` 时运行所有待执行的迁移；指定时回滚到目标版本。

---

## 2. YAML 配置说明

OntoAgent 使用 **7 个 YAML 配置文件**，全部位于 `src/ontoagent/config/` 和 `src/ontoagent/pipeline/` 目录下。

### 2.1 constraints.yaml — 约束定义（Layer 1）

定义本体约束的**遍历路径**和**传播规则**。`value_mapping` 由 `ONTOLOGY_CONSTRAINT_REGISTRY`（`domain/schema.py`）自动填充。

```yaml
traversal_constraints:
  data_sensitivity:
    source_label: "CodeEntity"
    relation_chain: ["PROCESSES_DATA"]
    target_label: "DataAsset"
    collect_property: "sensitivity"
    aggregation: "max"

propagation_rules:
  upstream_risk:
    along: ["CALLS"]
    direction: "backward"
    max_depth: 5
    collect_property: "entryCategory"
    aggregation: "exists"
```

### 2.2 constraint_overrides.yaml — 约束覆盖（Layer 2）

覆盖优先级高于 `constraints.yaml` 自动推导。支持三种操作：

- **patch**：局部修改已有约束（如将 `restricted` 从 `block` 降级为 `warn`）
- **allow_all**：单点白名单，指定实体绕过所有约束检查（支持 `expires` 过期时间）
- **add_constraint**：追加额外约束

### 2.3 ontology_actions.yaml — 操作注册表

定义 Agent 可执行的**操作意图**（Action），包含触发提示、绑定实体类型、提条件、约束守卫、关联函数等。

```yaml
actions:
  refactor:
    intent_type: refactor
    trigger_hint: "用户要求重构、拆分、优化代码"
    bind_to: code_entity
    submission_criteria: ["entity.lines > 100"]
    guard_configs:
      - type: traversal
        constraint: data_sensitivity
      - type: propagation
        rule: upstream_risk
    functions:
      - check_refactor_eligibility
    requires_approval: false
```

内置 6 种 Action：`refactor`、`document`、`analyze_impact`、`extract_interface`、`compliance_check`、`business_impact_analysis`。

### 2.4 business_ontology.yaml — 业务本体配置

定义**数据资产**（`data_assets`）和**合规条目**（`compliance_items`）：

```yaml
data_assets:
  - name: "用户个人信息"
    sensitivity: "confidential"
    data_type: "pii"
    aliases: ["PII", "用户数据"]

compliance_items:
  - name: "GDPR-17-删除权"
    regulation: "GDPR"
    severity: "critical"
```

### 2.5 function_danger_levels.yaml — 函数危险级别

定义每个 Function 的**危险级别**，用于审批策略判断：

| 级别 | 含义 | 示例 |
|------|------|------|
| `read` | 纯查询 | `query_entity`、`check_refactor_eligibility` |
| `read_sensitive` | 查询敏感数据 | `trace_business_impact` |
| `write` | 修改操作 | `generate_api_doc`、`update_entity` |
| `admin` | 系统级操作 | `create_entity` |

未列出的 Function 默认级别为 `read`。

### 2.6 approval_policy.yaml — 审批策略

控制 `ApprovalGate` 行为：启用哪些审批策略、BLOCK/WARN 结果如何处理、令牌配置。

```yaml
policies:
  - guard_result      # Guard Pipeline → 审批
  - action_approval   # Action.requires_approval → 审批
  - function_danger   # Function.danger_level → 审批

guard_result:
  on_block: require_approval
  on_warn: require_approval

token:
  ttl: 600            # 令牌有效期（秒）
  max_pending: 10     # 同时最多待审批数
```

### 2.7 tool_gateway.yaml — 工具网关配置

控制 Agent 通过 `graph_query` 工具执行 Cypher 的**写操作拦截**。

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

---

## 3. Agent 工具参考

OntoAgent Agent 通过 LangChain Tool 机制暴露 **10 个工具**，供 ReAct Agent 在推理循环中调用。所有工具返回 JSON 字符串。

### 3.1 graph_query

执行 Cypher 图查询，查询代码实体之间的关系。受 `tool_gateway.yaml` 写操作拦截保护。

| 参数 | 类型 | 说明 |
|------|------|------|
| `cypher` | str | Neo4j Cypher 查询语句 |

**常用查询模式**：

- 函数调用：`MATCH (a)-[:CALLS]->(b) WHERE a.name CONTAINS 'xxx' RETURN a.name, b.name`
- 模块依赖：`MATCH (m:ModuleEntity)-[:CONTAINS]->(c) RETURN m.name, c.name`
- 概念关联：`MATCH (c:ConceptEntity)<-[:DESCRIBES]-(e) RETURN c.name, e.name`

### 3.2 semantic_search

语义向量搜索，在 ChromaDB 中搜索与查询文本相似的代码实体。

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `query` | str（必填） | — | 搜索关键词或自然语言描述 |
| `top_k` | int | 5 | 返回结果数量，建议 5-10 |

**返回**: `[{file_path, function_name, score, ...}]`

### 3.3 express_intent

执行操作意图，**触发约束检查与审批流程**。是 Agent 执行写操作的核心入口。

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `intent_type` | str | `""` | 操作类型（正常模式必填） |
| `target` | str | `""` | 目标实体名称 |
| `params` | dict | `None` | 额外参数 |
| `approval_id` | str | `""` | 审批令牌（审批回执模式） |
| `approved` | bool | `false` | 是否批准（审批回执模式） |

**返回状态**:

| 状态 | 含义 |
|------|------|
| `"completed"` | 操作执行完成 |
| `"approval_required"` | 需要审批，返回 `approval_id` 和 `checks` |
| `"blocked"` | 被约束拦截，不可执行 |
| `"rejected"` | 审批被拒绝 |
| `"error"` | 执行出错 |

**审批完整流程**：

```
Agent 调用 express_intent(intent_type, target)
  → 约束检查（Guard Pipeline 五层守卫）
  → BLOCK → 返回 approval_required + approval_id
  → 前端渲染 ApprovalCard
  → 用户点击批准/拒绝
  → POST /api/chat/approval {approval_id, approved: true, thread_id}
  → 后端验证令牌 → 执行操作 → 返回结果
```

### 3.4 check_operation

**预查约束不执行**。在调用 `express_intent` 之前使用，了解操作是否会被拦截。

| 参数 | 类型 | 说明 |
|------|------|------|
| `intent_type` | str | 意图类型 |
| `target` | str | 目标实体名称 |

**返回**: `{pass: bool, checks: [{guard, level, reason}], block_reason: str|null}`

### 3.5 read_file

读取仓库中的文件内容，支持分页和行号标注。

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `path` | str（必填） | — | 文件路径（相对或绝对） |
| `offset` | int | 1 | 起始行号 |
| `limit` | int | 200 | 读取行数上限 |

### 3.6 list_directory

列出目录内容，按文件类型筛选。

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `path` | str | `"."` | 目录路径 |
| `glob` | str | `"*"` | 文件名匹配模式 |
| `recursive` | bool | `false` | 是否递归子目录 |

### 3.7 git_diff

获取 Git 仓库中指定范围的变更差异。

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `since` | str | `"HEAD~1"` | Git 引用对比基准 |
| `repo_path` | str | `"."` | 仓库路径 |

**返回**: `{since, total_changes, changed_files: [{status, file}]}`

### 3.8 git_log

查询 Git 提交历史。

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `count` | int | 10 | 返回条数 |
| `repo_path` | str | `"."` | 仓库路径 |
| `since` | str | — | 起始时间/引用 |

### 3.9 trace_history

查询当前会话（thread）的历史推理轨迹。

| 参数 | 类型 | 说明 |
|------|------|------|
| `thread_id` | str | 会话线程 ID |

**返回**: TraceLog 完整记录（steps 数组、approval 状态等）。

### 3.10 reflect

触发 Agent 元认知反思：分析当前 Trace 中的工具调用模式，归纳技能或提出优化建议。

| 参数 | 类型 | 说明 |
|------|------|------|
| `thread_id` | str | 要反思的 trace 线程 ID |

**返回**: 反思结果摘要（JSON），包含归纳的模式、建议的改进等。

---

## 4. REST API

**Base URL**: `http://localhost:8000/api/`

### 4.1 Chat — 同步对话

#### POST /api/chat/send

**请求**:

```json
{
  "message": "merge_node 的实现逻辑是什么",
  "thread_id": "optional-thread-id"
}
```

**响应**:

```json
{
  "answer": "...",
  "thread_id": "uuid",
  "duration_ms": 1234
}
```

### 4.2 Chat — 流式对话（SSE）

#### GET /api/chat/stream?thread_id=...&message=...

通过 Server-Sent Events (SSE) 推送 Agent 推理过程的实时事件流。前端通过 `@microsoft/fetch-event-source` 连接。

**SSE 事件类型**:

| 事件类型 | 数据结构 | 说明 |
|----------|----------|------|
| `token` | `{"type": "token", "content": "..."}` | LLM 输出的文本片段 |
| `tool_start` | `{"type": "tool_start", "tool_name": "graph_query", "args": {...}}` | 工具调用开始 |
| `tool_end` | `{"type": "tool_end", "tool_name": "graph_query", "result": "...", "duration_ms": 100}` | 工具调用完成 |
| `error` | `{"type": "error", "message": "..."}` | 执行出错 |
| `done` | `{"thread_id": "..."}` | 对话完成 |

**SSE 格式规范**:

```
event: token
data: {"type": "token", "content": "根据"}

event: token
data: {"type": "token", "content": "查询结果..."}

event: tool_start
data: {"type": "tool_start", "tool_name": "graph_query", "args": {"cypher": "MATCH ..."}}

event: tool_end
data: {"type": "tool_end", "tool_name": "graph_query", "duration_ms": 45}

event: done
data: {"thread_id": "abc-123"}
```

每条事件格式为 `event: <type>\ndata: <json>\n\n`，双换行分隔。

### 4.3 Chat — 审批决策

#### POST /api/chat/approval

前端审批卡片点击按钮后调用此端点，将用户决策发送回后端继续执行。

**请求体**:

```json
{
  "thread_id": "abc-123",
  "decision": "approved",
  "token": "approval-token-from-express-intent"
}
```

**决策值**: `"approved"` | `"rejected"`

**响应**:

```json
{
  "status": "ok",
  "result": {
    "success": true,
    "summary": "操作已完成",
    "results": [...]
  }
}
```

审批被拒绝时，`status` 仍为 `"ok"`，`result.success` 为 `false`。

### 4.4 Graph — 图谱查询

#### GET /api/graph/entity/{id}

获取实体详情（节点属性 + 双向关系）。

**响应**:

```json
{
  "id": "...",
  "name": "merge_node",
  "neo4jLabel": "CodeEntity",
  "properties": {"entityType": "function", "filePath": "..."},
  "relations": {
    "incoming": [{"source_id": "...", "source_name": "...", "type": "CALLS"}],
    "outgoing": [{"target_id": "...", "target_name": "...", "type": "CALLS"}]
  }
}
```

#### GET /api/graph/neighbors/{id}

获取实体的邻居节点和边。

#### GET /api/graph/stats

**响应**: `{"node_count": 150, "edge_count": 500, "by_type": {"CodeEntity": 100, "ConceptEntity": 20}}`

#### DELETE /api/graph/node/{node_id}

删除节点及其关联关系。

**响应**: `{"status": "deleted", "id": "..."}`

### 4.5 Trace — 推理轨迹

#### GET /api/trace/{thread_id}

获取指定线程的完整推理轨迹。

**响应**:

```json
{
  "thread_id": "abc-123",
  "query": "原始用户问题",
  "status": "completed",
  "steps": [
    {
      "step_id": 0,
      "type": "execution",
      "content": "Agent 开始推理...",
      "tool_name": null,
      "tool_args": null,
      "tool_result": null,
      "duration_ms": null
    },
    {
      "step_id": 1,
      "type": "tool_call",
      "content": "调用 graph_query",
      "tool_name": "graph_query",
      "tool_args": "{\"cypher\": \"MATCH ...\"}",
      "tool_result": "[{\"name\": \"...\"}]",
      "duration_ms": 45.2
    }
  ],
  "total_duration_ms": 5234.0
}
```

#### GET /api/trace/list

列出所有 traces 摘要（列表页用）：

```json
[
  {
    "thread_id": "...",
    "query": "...",
    "status": "completed",
    "step_count": 8,
    "total_duration_ms": 5000,
    "created_at": 1717000000.0,
    "approval_status": ""
  }
]
```

#### DELETE /api/trace/thread/{thread_id}

删除指定 trace。响应: `{"deleted": true}`

#### GET /api/trace/graph/mermaid

获取 Agent 状态图的 Mermaid 表示。响应: `{"mermaid": "graph TD\n..."}`

---

## 5. SSE 事件类型

流式对话 (`/api/chat/stream`) 通过 SSE 推送以下事件：

| 事件类型 | 触发时机 | 关键字段 |
|----------|----------|----------|
| `token` | LLM 输出每个文本片段 | `type: "token"`, `content: "..."` |
| `tool_start` | Agent 开始调用工具 | `type: "tool_start"`, `tool_name`, `args` |
| `tool_end` | 工具调用完成 | `type: "tool_end"`, `tool_name`, `result`, `duration_ms` |
| `error` | 执行出错或超时 | `type: "error"`, `message: "..."` |
| `done` | 对话正常完成 | `thread_id: "..."` |

**SSE 线路格式**:

```
data: {"type": "token", "content": "你好"}\n\n
```

每条消息以 `data: ` 开头，后跟 JSON，以 `\n\n` 结尾。事件类型同时出现在 `event:` 行和 `data.type` 字段中。

---

## 6. Trace API

### 6.1 TraceStep 类型

| 类型 | 含义 | 示例 |
|------|------|------|
| `execution` | Agent 执行/推理步骤 | LLM 思考输出 |
| `tool_call` | 工具调用记录 | `graph_query`、`semantic_search` 调用 |
| `approval_required` | 审批请求触发 | express_intent 被 BLOCK，等待审批 |
| `approval_resolved` | 审批已处理 | 用户批准或拒绝，操作继续或终止 |

### 6.2 TraceLog 字段

每条 Trace 记录包含以下关键字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `thread_id` | str | 唯一会话标识 |
| `query` | str | 原始用户问题 |
| `status` | str | `"running"` \| `"completed"` \| `"failed"` |
| `steps` | list[TraceStep] | 推理步骤列表 |
| `total_duration_ms` | float | 总耗时（毫秒） |
| `created_at` | float | 创建时间戳 |
| `approval_token` | str | 当前待审批的令牌（空字符串 = 无待审批） |
| `approval_status` | str | `"pending"` \| `"approved"` \| `"rejected"` \| `""` |
| `parent_trace_thread_id` | str | 审批回执关联的父 Trace ID |

### 6.3 审批关联 Trace

当 `express_intent` 触发审批时，TraceCollector 会：

1. 在当前 Trace 中添加 `approval_required` 步骤
2. 设置 `approval_token` 和 `approval_status = "pending"`
3. 前端审批完成后，通过 `POST /api/chat/approval` 传入 `thread_id`
4. 后端在同一个 Trace 中添加 `approval_resolved` 步骤
5. 更新 `approval_status` 为 `"approved"` 或 `"rejected"`

如果审批回执创建了新的 Agent 对话线程，新 Trace 的 `parent_trace_thread_id` 指向原 Trace。

---

## 7. MCP 集成

### 7.1 启动 MCP Server

```bash
ontoagent serve [--transport stdio|http] [--port 8000]
```

- **stdio 模式**（默认）：通过标准输入/输出与 MCP 客户端通信，适用于 Claude Code、Cursor 等 IDE。
- **HTTP 模式**：MCP 端点 `http://localhost:8000/mcp`。

### 7.2 MCP 工具列表

| 工具 | 参数 | 说明 |
|------|------|------|
| `semantic_search` | `query: str`, `k: int=10`, `entity_type: str=None` | ChromaDB 向量搜索 |
| `graph_query` | `cypher: str` | Neo4j 原生 Cypher 查询 |
| `impact_analysis` | `entity_id: str`, `depth: int=3` | 变更影响 BFS 分析 |
| `get_context` | `entity_id: str` | 节点 + 关系 + 相似实体 |
| `list_concepts` | （无） | 列出所有业务概念 |
| `get_module_tree` | （无） | 模块层次结构树 |
| `detect_changes` | `since: str`, `repo_path: str` | Git diff 变更检测 |
| `export_graph` | `format: str="json"` | 导出图数据（json/dot/cytoscape） |

### 7.3 Claude Code 集成

在项目 `.claude/settings.json` 中配置：

```json
{
  "mcpServers": {
    "ontoagent": {
      "command": "uv",
      "args": ["run", "ontoagent", "serve"]
    }
  }
}
```

---

## 8. 环境变量

所有环境变量使用 `ONTOAGENT_` 前缀：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ONTOAGENT_NEO4J_URI` | `bolt://localhost:7687` | Neo4j 连接地址 |
| `ONTOAGENT_NEO4J_USER` | `neo4j` | Neo4j 用户名 |
| `ONTOAGENT_NEO4J_PASSWORD` | — | Neo4j 密码 |
| `ONTOAGENT_OLLAMA_URL` | `http://localhost:11434` | Ollama 服务地址 |
| `ONTOAGENT_EMBEDDING_MODEL` | `qwen2.5-coder:0.5b` | 嵌入模型名 |
| `ONTOAGENT_LLM_MODEL` | — | LLM 模型名 |
| `ONTOAGENT_AGENT_LLM_PROVIDER` | `zhipu` | Agent LLM 提供商 |
| `ONTOAGENT_AGENT_LLM_MODEL` | — | Agent LLM 模型 |
| `ONTOAGENT_AGENT_API_KEY` | — | Agent LLM API Key |
| `ONTOAGENT_AGENT_BASE_URL` | — | Agent LLM API 地址 |
| `ONTOAGENT_CHROMA_DIR` | `.chroma` | ChromaDB 持久化目录 |
| `ONTOAGENT_SEMANTIC_LLM_PROVIDER` | `ollama` | 语义提取 LLM 提供商 |
| `ONTOAGENT_SEMANTIC_API_KEY` | — | 语义提取 API Key |
| `ONTOAGENT_SEMANTIC_BASE_URL` | — | 语义提取 API 地址 |

---

> **版本**: OntoAgent v0.1.0 | **最后更新**: 2026-06-28
