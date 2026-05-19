---
title: LayerKG API与集成参考手册
date: 2026-05-17T16:45:54+08:00
lastmod: 2026-05-17T16:45:54+08:00
---

# LayerKG API与集成参考手册

# LayerKG API & 集成参考手册

## 1. CLI 命令参考

### 1.1 全局选项

```
layerkg [OPTIONS] COMMAND [ARGS]...

选项:
  -v, --verbose  详细输出（DEBUG 日志级别）
  --help         显示帮助
```

### 1.2 build — 全量构建

```bash
layerkg build REPO_PATH [OPTIONS]
```

|选项|类型|默认|说明|
| ------| --------------| -------| -----------------------|
|​`REPO_PATH`|路径（必填）|—|目标仓库目录|
|​`--skip-semantic`|flag|false|跳过 Stage 3 语义提取|
|​`--skip-clustering`|flag|false|跳过 Stage 4 模块聚类|
|​`--verbose-build`|flag|false|逐阶段输出详情|
|​`--clear`|flag|false|清空数据库后重建|

**示例**:

```bash
layerkg build ./my-project
layerkg build ./my-project --skip-semantic --skip-clustering
layerkg build ./my-project --clear --verbose-build
```

**输出**: `Build complete: X files scanned, Y entities created, Z relations created`

### 1.3 query — 语义搜索

```bash
layerkg query TEXT [OPTIONS]
```

|选项|类型|默认|说明|
| ------| --------------| ------| --------------|
|​`TEXT`|文本（必填）|—|搜索查询|
|​`-t, --type`|文本|—|实体类型过滤|
|​`-n, --limit`|整数|10|返回数量|

**示例**:

```bash
layerkg query "数据库连接"
layerkg query "解析器" -t class -n 5
```

### 1.4 update — 增量更新

```bash
layerkg update REPO_PATH [OPTIONS]
```

|选项|类型|默认|说明|
| ------| --------------| -------| ------------------------|
|​`REPO_PATH`|路径（必填）|—|目标仓库目录|
|​`--since`|文本|​`HEAD~1`|Git 对比基准|
|​`--dry-run`|flag|false|只检测不执行|
|​`--full-scan`|flag|false|全量扫描（不依赖 Git）|

### 1.5 ask — Agent 对话

```bash
layerkg ask [QUESTION] [OPTIONS]
```

|选项|类型|默认|说明|
| ------| --------------| -------| ----------|
|​`QUESTION`|文本（可选）|—|单次问题|
|​`-i, --interactive`|flag|false|交互模式|

**示例**:

```bash
layerkg ask "merge_node 被谁调用"
layerkg ask -i  # 进入交互模式，quit/exit 退出
```

### 1.6 serve — MCP Server

```bash
layerkg serve [OPTIONS]
```

|选项|类型|默认|说明|
| ------| -------------| ------| ---------------|
|​`--transport`|​`stdio`​\|`http`|​`stdio`|传输协议|
|​`--port`|整数|8000|HTTP 模式端口|

### 1.7 web — Web API Server

```bash
layerkg web [OPTIONS]
```

|选项|类型|默认|说明|
| ------| ------| -------| ------------|
|​`--host`|文本|​`0.0.0.0`|监听地址|
|​`--port`|整数|8000|监听端口|
|​`--reload`|flag|false|开发热重载|

### 1.8 info — 配置和状态

```bash
layerkg info
```

显示: Neo4j URI、Ollama URL、嵌入模型、ChromaDB 路径、ChromaDB 实体数。

### 1.9 version — 版本信息

```bash
layerkg version
```

显示: LayerKG 版本、Python 版本、Neo4j URI。

### 1.10 butler — Butler Engine 子命令

```bash
layerkg butler serve [OPTIONS]   # 启动后台监控
layerkg butler update [OPTIONS]  # 手动增量更新
layerkg butler build [OPTIONS]   # 手动全量构建
layerkg butler status            # 查看引擎状态
```

#### butler serve

|选项|类型|默认|说明|
| ------| ---------------------------| ------| ----------------|
|​`-r, --repo`|路径|​`.`|监控的仓库路径|
|​`-p, --poll-interval`|浮点|30.0|轮询间隔（秒）|
|​`--log-level`|​`DEBUG`​\|`INFO`​\|`WARNING`​\|`ERROR`|​`INFO`|日志级别|

#### butler update

|选项|类型|默认|说明|
| ------| ------| ------| --------------|
|​`-r, --repo`|路径|​`.`|仓库路径|
|​`-s, --since`|文本|​`HEAD~1`|Git 对比基准|

#### butler build

|选项|类型|默认|说明|
| ------| ------| ------| ----------|
|​`-r, --repo`|路径|​`.`|仓库路径|

## 2. MCP 工具参考

### 2.1 连接方式

**stdio 模式**（推荐用于 Claude Code / Cursor 集成）:

```json
// ~/.claude/settings.json 或项目 .claude/settings.json
{
  "mcpServers": {
    "layerkg": {
      "command": "uv",
      "args": ["run", "layerkg", "serve"]
    }
  }
}
```

**HTTP 模式**:

```bash
layerkg serve --transport http --port 8000
# MCP 端点: http://localhost:8000/mcp
```

### 2.2 工具列表

|工具|参数|返回|说明|
| ------| --------------| ------| -----------------------|
|​`semantic_search`|​`query: str`​, `k: int=10`​, `entity_type: str=None`|​`list[dict]`|ChromaDB 向量搜索|
|​`graph_query`|​`cypher: str`|​`list[dict]`|Neo4j 原生 Cypher|
|​`impact_analysis`|​`entity_id: str`​, `depth: int=3`|​`dict`|变量深度 BFS 路径查询|
|​`get_context`|​`entity_id: str`|​`dict`|节点+关系+相似实体|
|​`list_concepts`|（无参数）|​`list[dict]`|列出所有概念实体|
|​`get_module_tree`|（无参数）|​`dict`|模块层次结构|
|​`detect_changes`|​`since: str="HEAD~1"`​, `repo_path: str="."`|​`dict`|Git diff 变更检测|
|​`export_graph`|​`format: str="json"`|​`dict`|导出图数据|

**export_graph format 选项**:

- ​`"json"`​ — `{nodes: [...], edges: [...]}`
- ​`"dot"` — Graphviz DOT 格式字符串
- ​`"cytoscape"` — Cytoscape.js 兼容格式

## 3. REST API 参考

**Base URL**: `http://localhost:8000/api`

### 3.1 Chat — 对话

#### POST /api/chat

同步对话。

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

#### POST /api/chat/stream

SSE 流式对话。

**请求**: 同上

**SSE 事件**:

```
event: token
data: {"type": "token", "content": "...", "thread_id": "..."}

event: token
data: {"type": "tool_start", "tool_name": "semantic_search", "args": {...}}

event: token
data: {"type": "tool_end", "tool_name": "semantic_search", "duration_ms": 100}

event: token
data: {"type": "done", "thread_id": "...", "duration_ms": 5000}
```

### 3.2 Graph — 图数据

#### GET /api/graph/stats

**响应**:

```json
{
  "nodes": {"CodeEntity": 100, "ConceptEntity": 20, ...},
  "total_edges": 500
}
```

#### GET /api/graph

**参数**:

|参数|类型|默认|说明|
| ------| ------| ------| ---------------------------|
|​`center`|str|null|居中节点名称（null=全图）|
|​`depth`|int|1|扩展深度 (1-3)|
|​`limit`|int|100|节点数限制 (1-500)|
|​`type`|str|null|标签过滤（逗号分隔）|

**响应**:

```json
{
  "nodes": [{"id": "...", "label": "CodeEntity", "name": "...", "properties": {...}}],
  "edges": [{"source": "id1", "target": "id2", "type": "CALLS", "properties": {...}}]
}
```

#### GET /api/graph/node/{node_id}

**响应**:

```json
{
  "id": "...",
  "label": "CodeEntity",
  "name": "merge_node",
  "properties": {...},
  "relations": {
    "incoming": [...],
    "outgoing": [...]
  }
}
```

#### DELETE /api/graph/node/{node_id}

**响应**: `{"deleted": true}`

### 3.3 Trace — 推理轨迹

#### GET /api/trace/list

**响应**:

```json
[
  {"thread_id": "...", "query": "...", "status": "completed", "duration_ms": 5000, "step_count": 8}
]
```

#### GET /api/trace/thread/{thread_id}

**响应**:

```json
{
  "thread_id": "...",
  "query": "...",
  "status": "completed",
  "duration_ms": 5000,
  "steps": [
    {"step_id": 1, "type": "thinking", "content": "..."},
    {"step_id": 2, "type": "tool_call", "tool_name": "semantic_search", "args": {...}},
    {"step_id": 3, "type": "tool_result", "content": "...", "duration_ms": 100}
  ]
}
```

#### GET /api/trace/graph/mermaid

**响应**:

```json
{
  "mermaid": "graph TD\n  agent --> tools\n  ..."
}
```

#### DELETE /api/trace/thread/{thread_id}

**响应**: `{"deleted": true}`

### 3.4 Health Check

#### GET /health

**响应**: `{"status": "ok"}`

## 4. Butler 事件参考

### 4.1 事件类型

|事件类型|来源|Payload 字段|说明|
| ----------| --------| ------------------| ------------------|
|​`code.changed`|​`git_watcher`|​`since`​, `repo_path`​, `full_scan`​, `file_extension`|检测到代码变更|
|​`build.full`|​`cli`|​`repo_path`|手动全量构建请求|
|​`handler.completed`|engine|​`handler_id`​, `event_type`​, `file_extension`​, `result`|Handler 成功|
|​`handler.failed`|engine|​`handler_id`​, `error`|Handler 失败|

### 4.2 Handler 注册

|Handler ID|监听事件|说明|
| ------------| ----------| ------------------|
|​`knowledge.update`|​`code.changed`|增量更新知识图谱|
|​`knowledge.full_build`|​`build.full`|全量构建知识图谱|
|​`butler.reflection`|​`handler.completed`|反思归纳技能模式|

### 4.3 技能状态流转

```
candidate (confidence < 0.8)
    │
    │ hit_count 增加，confidence 上升
    │ confidence = 0.5 + hit_count × 0.1 (上限 1.0)
    ▼
active (confidence ≥ 0.8)
    │
    │ 手动标记废弃
    ▼
deprecated (软删除)
```

### 4.4 SkillStore API

|方法|说明|
| ------| ------------------------------|
|​`create(skill: SkillEntity) -> str`|创建技能|
|​`get(skill_id) -> SkillEntity \| None`|按ID查询|
|​`update(skill_id, **kwargs) -> bool`|部分更新|
|​`delete(skill_id) -> bool`|软删除（status→deprecated）|
|​`list_by_layer(layer, status=None)`|按层级+状态筛选|
|​`count_by_layer() -> dict`|各层级数量统计|
|​`search_by_pattern(key, value)`|按 pattern 字段搜索|
|​`get_candidates(min_confidence=0.5)`|获取候选技能|
|​`increment_hit_count(skill_id)`|原子计数+1|

## 5. Python SDK 参考

### 5.1 编程方式使用 Builder

```python
from pathlib import Path
from layerkg.config import LayerKGConfig
from layerkg.builder import LayerKGBuilder

config = LayerKGConfig.from_env()

with LayerKGBuilder(config) as builder:
    # 全量构建
    result = builder.build(Path("./my-project"))
    print(f"Created {result.entities_created} entities")

    # 语义搜索
    results = builder.query("数据库连接", n_results=5)
    for r in results:
        print(r["metadata"]["name"], r.get("distance"))

    # 查看状态
    info = builder.info()
    print(info)
```

### 5.2 编程方式增量更新

```python
from pathlib import Path
from layerkg.config import LayerKGConfig
from layerkg.incremental_updater import IncrementalUpdater

config = LayerKGConfig.from_env()

with IncrementalUpdater(config, repo_path=Path("./my-project")) as updater:
    # Git diff 增量更新
    report = updater.update(since="HEAD~3")
    print(f"Detected {report.changes_detected} changes")
    print(f"Updated {report.nodes_updated} nodes")

    # dry-run 模式
    report = updater.update(since="HEAD~1", dry_run=True)
    print(f"Would update {report.impacted_nodes_count} nodes")
```

### 5.3 直接访问存储

```python
from layerkg.config import LayerKGConfig
from layerkg.neo4j_store import Neo4jGraphStore
from layerkg.chroma_store import ChromaStore

config = LayerKGConfig.from_env()

# Neo4j
with Neo4jGraphStore(config.neo4j_uri, config.neo4j_user, config.neo4j_password) as store:
    store.ensure_constraints()
    node = store.get_node("some-uuid")
    results = store.query("MATCH (n:CodeEntity) WHERE n.name = $name RETURN n", {"name": "merge_node"})

# ChromaDB
with ChromaStore(persist_dir=config.chroma_persist_dir, ollama_url=config.ollama_base_url, embedding_model=config.embedding_model) as cs:
    results = cs.search("查询文本", n_results=10, where={"entity_type": "function"})
```

### 5.4 编程方式使用 Agent

```python
import asyncio
from layerkg.agent.graph import run_query, run_query_stream
from layerkg.agent.trace import TraceCollector

# 同步查询
answer = asyncio.run(run_query("merge_node 被谁调用"))
print(answer)

# 流式查询 + Trace
collector = TraceCollector()

async def stream_query():
    async for event in run_query_stream("查询问题", thread_id="test", trace_collector=collector):
        if event["type"] == "token":
            print(event["content"], end="")
        elif event["type"] == "tool_start":
            print(f"\n[Tool: {event['tool_name']}]")

asyncio.run(stream_query())
```

## 6. 常用 Cypher 查询模板

### 查找函数及其调用者

```cypher
MATCH (caller:CodeEntity)-[:CALLS]->(target:CodeEntity {name: 'merge_node'})
RETURN caller.name, caller.file_path
```

### 查找类的继承链

```cypher
MATCH path = (child:CodeEntity)-[:EXTENDS*1..3]->(parent:CodeEntity)
WHERE child.entity_type = 'class'
RETURN path
```

### 查找模块的所有函数

```cypher
MATCH (m:CodeEntity {entity_type: 'module'})-[:CONTAINS]->(f:CodeEntity {entity_type: 'function'})
WHERE m.file_path CONTAINS 'neo4j_store'
RETURN f.name, f.start_line
ORDER BY f.start_line
```

### 查找影响某个函数的所有概念

```cypher
MATCH (c:ConceptEntity)-[:SEMANTIC_IMPACT]->(f:CodeEntity {name: 'merge_node'})
RETURN c.name, c.entity_type
```

### 查找两个函数之间的调用路径

```cypher
MATCH path = shortestPath(
  (a:CodeEntity {name: 'build'})-[:CALLS*]-(b:CodeEntity {name: 'merge_node'})
)
RETURN path
```

### 统计各类型实体数量

```cypher
MATCH (n:CodeEntity)
RETURN n.entity_type AS type, count(*) AS count
ORDER BY count DESC
```

### 查找无关系的孤立节点

```cypher
MATCH (n:CodeEntity)
WHERE NOT (n)--()
RETURN n.name, n.file_path
```
