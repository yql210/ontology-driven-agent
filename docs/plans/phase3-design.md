# Phase 3 方案设计：LangGraph ReAct Agent 编排层（v2 修订版）

> 审核历史：v1 评分 8.2/10，已按审核意见修订

## 一、问题背景

Phase 1-2.5 已完成知识图谱构建引擎，能从代码仓库提取实体/关系到 Neo4j + ChromaDB。现在需要一个 Agent 编排层，让用户用自然语言查询代码架构、分析影响范围、生成报告。

**当前已有基础设施：**
- `src/layerkg/mcp_server.py` — FastMCP Server，暴露 8 个工具（semantic_search, graph_query, impact_analysis, get_context, list_concepts, get_module_tree, detect_changes, export_graph）
- `src/layerkg/neo4j_store.py` — Neo4jGraphStore 图数据库操作（context manager + `.query()` 方法）
- `src/layerkg/chroma_store.py` — ChromaStore 向量存储（`.search()` 方法）
- `src/layerkg/aligner.py` — ConceptAligner（`.list_concepts()` 方法）
- `src/layerkg/module_clustering.py` — ModuleClustering（`.get_module_tree()` 方法）
- `src/layerkg/impact_propagator.py` — ImpactPropagator（`.compute_impact()` / `.propagate()` 方法）
- `src/layerkg/change_detector.py` — ChangeDetector（`.detect_changes()` 方法）
- `src/layerkg/cli.py` — Click CLI 入口（build 命令已有）
- Neo4j 数据：~311 CodeEntity + ~60 ConceptEntity + ~18 ModuleEntity + ~749 关系
- ChromaDB 向量库：~1700 embeddings

## 二、核心设计决策（已确认）

1. **ReAct 模式**（非固定路由5个Agent）— LLM 自主决定调什么工具、调几次、什么顺序
2. **智谱 Anthropic 兼容接口** — `ChatAnthropic(model="claude-sonnet-4-20250514", base_url="https://open.bigmodel.cn/api/anthropic")`
3. **Langfuse 可观测性** — Phase 3 就集成，全链路 trace
4. **复用现有模块的底层函数** — Agent 工具直接调用 ChromaStore/Neo4jGraphStore 等现有类的方法

## 三、架构设计

### 3.1 整体架构

```
用户 → CLI (layerkg ask) → LangGraph StateGraph
                                  ↓
                           Agent (LLM) ← system prompt + 8 tools
                              ↕ (循环，max_iterations=10)
                           ToolNode (8 tools)
                                  ↓
                           Langfuse (全链路 trace)
```

### 3.2 AgentState（极简设计）

```python
from langgraph.graph import MessagesState

# ReAct 模式只需 messages 字段
# MessagesState 已内置 messages: Annotated[list, add_messages]
class AgentState(MessagesState):
    pass
```

**不设计 intent/query_result 等字段**的原因：
- ReAct 模式下 LLM 自己决定推理路径，不需要外部传入意图
- 工具调用结果自动通过 messages 传递（tool message）
- 保持状态极简，避免过度设计

**循环控制：**
- `recursion_limit=25`（LangGraph 默认值，防止状态图死循环）
- Agent 节点内部追踪工具调用次数，超过 `max_iterations=10` 时强制返回最终回答

### 3.3 LangGraph 状态图

```python
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, tools_condition

def create_agent_graph(tools, llm_with_tools):
    graph = StateGraph(AgentState)
    
    # 节点
    graph.add_node("agent", agent_node)      # LLM 推理节点
    graph.add_node("tools", ToolNode(tools))  # 工具执行节点
    
    # 边
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    
    return graph.compile(recursion_limit=25)
```

### 3.4 Agent 节点函数

```python
async def agent_node(state: AgentState) -> dict:
    """LLM 推理节点：接收 messages，返回 LLM 响应"""
    system_prompt = AGENT_SYSTEM_PROMPT  # 静态常量
    messages = [SystemMessage(content=system_prompt)] + state["messages"]
    response = await llm_with_tools.ainvoke(messages)
    return {"messages": [response]}
```

### 3.5 工具封装

将现有模块的方法封装为 LangChain Tool。**核心原则：不重复实现逻辑，直接调用现有类的方法。**

```python
from langchain_core.tools import tool

@tool
async def semantic_search(query: str, top_k: int = 5) -> str:
    """语义搜索：在代码库中搜索与 query 相关的代码片段。
    
    Args:
        query: 搜索关键词或自然语言描述
        top_k: 返回结果数量，建议 5-10
    
    Returns:
        匹配的代码片段列表，包含文件路径、函数名、相似度分数
    """
    chroma = _get_chroma()  # 复用 mcp_server 的辅助函数模式
    results = chroma.search(query_text=query, n_results=top_k)
    return json.dumps(results, ensure_ascii=False)

@tool
async def graph_query(cypher: str) -> str:
    """执行 Cypher 图查询，查询代码实体之间的关系。
    
    常用查询模式：
    - 查找函数调用关系：MATCH (a)-[:CALLS]->(b) WHERE a.name CONTAINS 'xxx' RETURN a,b
    - 查找模块依赖：MATCH (m:ModuleEntity)-[:CONTAINS]->(c) RETURN m,c
    - 查找概念关联：MATCH (c:ConceptEntity)<-[:DESCRIBES]-(e) RETURN c,e
    
    Args:
        cypher: Neo4j Cypher 查询语句
    
    Returns:
        查询结果的 JSON 格式字符串
    """
    neo4j = _get_neo4j()
    results = neo4j.query(cypher)
    return json.dumps(results, ensure_ascii=False)
```

**共享辅助函数（`agent/_helpers.py`，MCP Server 和 Agent 都调用）：**

从 `mcp_server.py` 提取 `_get_neo4j()`, `_get_chroma()`, `_get_config()` 等辅助函数到共享模块，避免代码重复。

```python
# src/layerkg/agent/_helpers.py
from layerkg.config import LayerKGConfig
from layerkg.neo4j_store import Neo4jGraphStore
from layerkg.chroma_store import ChromaStore

_config: LayerKGConfig | None = None
_neo4j: Neo4jGraphStore | None = None
_chroma: ChromaStore | None = None

def get_config() -> LayerKGConfig:
    global _config
    if _config is None:
        _config = LayerKGConfig.from_env()
    return _config

def get_neo4j() -> Neo4jGraphStore:
    global _neo4j
    if _neo4j is None:
        cfg = get_config()
        _neo4j = Neo4jGraphStore(cfg.neo4j_uri, cfg.neo4j_user, cfg.neo4j_password)
    return _neo4j

def get_chroma() -> ChromaStore:
    global _chroma
    if _chroma is None:
        cfg = get_config()
        _chroma = ChromaStore(cfg.chroma_persist_dir, cfg.ollama_base_url, cfg.embedding_model)
    return _chroma

def get_aligner() -> ConceptAligner:
    global _aligner
    if _aligner is None:
        _aligner = ConceptAligner(chroma_store=get_chroma(), neo4j_store=get_neo4j())
    return _aligner

def get_clustering() -> ModuleClustering:
    global _clustering
    if _clustering is None:
        _clustering = ModuleClustering(neo4j_store=get_neo4j())
    return _clustering

def get_change_detector(repo_path: str = ".") -> GitChangeDetector:
    global _change_detector
    if _change_detector is None:
        _change_detector = GitChangeDetector(Path(repo_path))
    return _change_detector
```

**graph_query 安全措施：**
- try-catch 包裹 `neo4j.query()`，捕获语法错误返回友好提示
- 不做 Cypher 白名单验证（LLM 生成的 Cypher 只读查询为主，写入操作在 ReAct 场景极少）
- 如后续需要，可在 `Neo4jGraphStore.query()` 层加 read-only 事务标记

8个工具清单（修正后）：

| 工具 | 功能 | 底层调用 |
|------|------|---------|
| semantic_search | 语义搜索代码 | `ChromaStore.search()` |
| graph_query | Cypher 图查询 | `Neo4jGraphStore.query()` |
| impact_analysis | 变更影响分析 | MCP Server 的 Cypher BFS 实现（参考 `mcp_server.py:131-166`） |
| get_context | 获取代码上下文 | `Neo4jGraphStore.get_node()` + `.get_relations()` + `ChromaStore.get_entity()` |
| list_concepts | 列出概念/设计模式 | `ConceptAligner.list_concepts()` |
| get_module_tree | 模块结构树 | `ModuleClustering.get_module_tree()` |
| detect_changes | 检测代码变更 | `GitChangeDetector.detect_changes()` |
| export_graph | 导出图谱数据 | `Neo4jGraphStore.query()` |

### 3.6 System Prompt 设计

```python
AGENT_SYSTEM_PROMPT = """你是 LayerKG 代码知识图谱助手。你可以帮助用户理解代码架构、查询依赖关系、分析变更影响。

【工具列表】
1. semantic_search - 语义搜索代码片段（top_k 建议 5-10）
2. graph_query - 执行 Cypher 图查询（关系、依赖、调用链）
3. impact_analysis - 分析代码变更的影响范围（depth 建议 2-4）
4. get_context - 获取函数/类的完整上下文
5. list_concepts - 列出项目中的概念和设计模式
6. get_module_tree - 查看项目的模块结构
7. detect_changes - 检测最近的代码变更
8. export_graph - 导出知识图谱数据

【Schema 参考】
节点标签: CodeEntity, DocEntity, ConceptEntity, ModuleEntity, ResourceEntity
关系类型: CALLS, IMPORTS, CONTAINS, EXTENDS, IMPLEMENTS, DESCRIBES, ILLUSTRATES, DERIVED_FROM, SEMANTIC_IMPACT

常用属性:
- CodeEntity: name, file_path, start_line, end_line, entity_type(function/class/module), docstring, code_parameters
- ConceptEntity: name, entity_type(business_concept/design_pattern/api_contract/data_model/process), description
- ModuleEntity: name, size, description

【工作流程】
1. 理解用户问题，选择合适的工具
2. 执行工具，分析结果
3. 如需更多信息，调用其他工具（最多 10 轮工具调用）
4. 综合结果，给出清晰的自然语言回答

【错误处理】
- 如果 graph_query 返回语法错误，检查 Cypher 是否合法，修正后重试
- 如果工具超时，减少结果数量（降低 top_k）重试
- 如果查询无结果，告知用户并建议换搜索关键词或用 semantic_search 替代 graph_query

【查询技巧】
- graph_query 的 cypher 参数必须是合法的 Neo4j Cypher 语句
- 查询时优先用 name 和 file_path 属性定位实体
- 影响分析优先用 impact_analysis 工具，不要自己写 BFS 的 Cypher
"""
```

### 3.7 CLI 集成

```python
# cli.py 新增 ask 命令
@cli.command()
@click.argument('question', required=False)
@click.option('--interactive', '-i', is_flag=True, help='交互式对话模式')
def ask(question, interactive):
    """向代码知识图谱提问"""
    import asyncio
    if interactive or not question:
        # 交互式循环
        ...
    else:
        # 单次问答（agent.ainvoke 是异步的，用 asyncio.run 包裹）
        result = asyncio.run(agent.ainvoke(
            {"messages": [HumanMessage(content=question)]}
        ))
        click.echo(result["messages"][-1].content)
```

### 3.8 Langfuse 集成

**Phase 3 方案：** 先用 Langfuse 本地自托管（Docker），在远程服务器 `<YOUR_SERVER_IP>` 上部署。

```python
from langfuse.callback import CallbackHandler

langfuse_handler = CallbackHandler(
    public_key="...",           # Docker 部署后配置
    secret_key="...",
    host="http://<YOUR_SERVER_IP>:3000"  # 远程服务器 Docker 部署
)

# 在 agent invoke 时传入
result = agent.invoke(
    {"messages": [HumanMessage(content=question)]},
    config={"callbacks": [langfuse_handler]}
)
```

**部署步骤（Day 2 执行）：**
```bash
# 在远程服务器上
docker run -d --name langfuse \
  -p 3000:3000 \
  -e DATABASE_URL=postgresql://... \
  langfuse/langfuse:2
```

**降级方案：** 如果 Docker 部署受阻，临时用 Langfuse 云端免费版（`host="https://cloud.langfuse.com"`），无需本地部署。

## 四、目录结构

```
src/layerkg/
├── agent/
│   ├── __init__.py         # 导出 create_agent, run_agent + Agent 配置
│   ├── graph.py            # LangGraph 状态图构建
│   ├── tools.py            # 8 个工具的 LangChain Tool 封装
│   ├── prompt.py           # System Prompt 常量
│   └── _helpers.py         # 共享辅助函数（get_neo4j, get_chroma 等）
├── cli.py                  # 新增 ask 命令
└── mcp_server.py           # 保持不变，后续可改为调用 _helpers.py
```

**说明：** 原 `agent/config.py` 合并到 `agent/__init__.py`（配置项少，不值得单独文件）。`_helpers.py` 前缀下划线表示内部模块。

## 五、5 天实施计划

### Day 1：Agent 骨架 + API 验证

**目标：** `layerkg ask "问题"` 能跑通一个完整的工具调用循环

| # | 任务 | 说明 |
|---|------|------|
| 1 | 安装依赖 | `uv add langgraph langchain-anthropic langfuse langfuse-python` |
| 2 | 智谱 API 兼容性测试 | 10行脚本验证 ChatAnthropic + base_url 是否正常工作（工具调用、流式响应） |
| 3 | 新建 agent/ 目录 | `__init__.py`, `graph.py`, `tools.py`, `prompt.py`, `_helpers.py` |
| 4 | 提取 _helpers.py | 从 mcp_server.py 提取 get_neo4j/get_chroma/get_config |
| 5 | 封装 3 个核心工具 | semantic_search, graph_query, get_context |
| 6 | 实现 AgentState + 状态图 | ReAct 循环：agent ↔ tools，recursion_limit=25 |
| 7 | 配置智谱 Anthropic 接口 | ChatAnthropic + base_url + api_key（从 .env 读取） |
| 8 | CLI ask 命令（单次模式） | `layerkg ask "merge_node 被谁调用"` |

**Day 1 验收：** `uv run layerkg ask "merge_node 被谁调用"` 能返回包含调用者的自然语言回答。

### Day 2：Agent 完善 + Langfuse

**目标：** Agent 能自主推理多步 + Langfuse 看 trace

| # | 任务 | 说明 |
|---|------|------|
| 1 | 封装剩余 5 个工具 | impact_analysis, list_concepts, get_module_tree, detect_changes, export_graph |
| 2 | Langfuse Docker 部署 | 远程服务器 `docker run langfuse` |
| 3 | Langfuse CallbackHandler 集成 | trace 回调注入 agent.invoke |
| 4 | System Prompt 调优 | 根据实际调用情况调整工具使用引导 |
| 5 | 对话记忆（MemorySaver） | LangGraph 内置 checkpointer |
| 6 | 异常处理与重试 | 工具调用失败 → 返回错误信息给 LLM 重试 |

### Day 3：CLI 完善 + 基础测试

**目标：** 可演示的 CLI + 基础测试覆盖

| # | 任务 | 说明 |
|---|------|------|
| 1 | CLI 交互式模式 | `layerkg ask -i` |
| 2 | 单元测试 | 工具封装测试、状态流转测试 |
| 3 | 集成测试 | 端到端 query（用 `unittest.mock` mock LLM API） |
| 4 | ruff + 类型检查 | 代码质量 |

### Day 4：自建评估集 25 题

**目标：** 25 题评估结果 + 报告

| # | 任务 | 说明 |
|---|------|------|
| 1 | 评估框架 | 评分脚本 + 比对逻辑 |
| 2 | Level 1 单工具 10 题 | 跑通 + 评分 |
| 3 | Level 2 多步推理 10 题 | 跑通 + 评分 |
| 4 | Level 3 综合 5 题 | 跑通 + 评分 |
| 5 | 评估报告 | 自动生成 |

### Day 5：优化 + 外部基准适配

**目标：** Phase 3 完成

| # | 任务 | 说明 |
|---|------|------|
| 1 | 优化 System Prompt | 根据评估结果调整 |
| 2 | SWE-bench 筛选 | 20-30 题适配 |
| 3 | RepoQA 适配 | 20 题适配 |
| 4 | 最终提交 + 文档 | Phase 3 完成 |

## 六、不改什么（边界）

1. **不改 MCP Server 的核心逻辑** — Agent 工具直接调用底层类方法（ChromaStore/Neo4jGraphStore/ConceptAligner 等），MCP Server 后续可改为调用 `_helpers.py`
2. **不改构建管线** — Phase 1-2.5 的 build/incremental 逻辑不动
3. **不改 Neo4j Schema** — 实体/关系类型保持现有设计
4. **不做 Web UI** — Phase 4 的事
5. **不改现有测试** — 702 tests 保持全部通过

## 七、依赖关系

```
新增依赖：
- langgraph >= 0.4              # Agent 状态图框架
- langchain-anthropic           # Anthropic 兼容 LLM
- langfuse >= 2.50.0            # 可观测性（Python SDK）

已有依赖（复用）：
- langchain-core                # Tool、Message 基类（langgraph 依赖链自带）
- neo4j                         # 图数据库驱动
- chromadb                      # 向量数据库
- click                         # CLI 框架

注意：
- langchain-core 无需单独安装，langgraph 会自动带入
- langfuse-python 包名就是 langfuse（pip install langfuse）
```

## 八、风险与缓解

| 风险 | 严重度 | 缓解策略 |
|------|--------|---------|
| 智谱 Anthropic 接口不完全兼容 | 🔴 高 | Day 1 首先验证 API；B计划用 `langchain-openai` + OpenAI 兼容接口 |
| ReAct Agent 调用工具次数过多 | 🟠 中 | `recursion_limit=25` + Agent 内部 max_iterations=10 + Prompt 引导高效推理 |
| LLM 生成的 Cypher 不合法 | 🟠 中 | graph_query 工具内加 try-catch + 友好错误提示返回给 Agent 自修正 |
| Cypher 注入风险 | 🟠 中 | 当前场景只读查询为主；如需防护可在 Neo4jGraphStore.query() 层加 read-only 事务 |
| Neo4j/ChromaDB 资源泄漏 | 🟡 低 | _helpers.py 的 lazy init 实例全局复用（单例模式），不用每次创建新连接 |
| LLM 幻觉（生成不存在的实体名） | 🟡 低 | Prompt 引导查询无结果时告知用户 + 建议用 semantic_search 替代 |
| ChromaDB 向量查询慢 | 🟡 低 | 工具内限制 top_k(默认5) + 添加超时 |
| 并发问题（多用户同时 ask） | 🟡 低 | Neo4j driver 本身线程安全；Phase 3 是 CLI 单用户，Phase 4 Web 时再处理 |

## 九、与现有代码的交互点（已验证）

Agent 工具底层直接调用现有模块的方法：

| 工具 | 调用目标 | 文件 | 方法 |
|------|---------|------|------|
| semantic_search | ChromaStore | `chroma_store.py` | `.search(query_text=..., n_results=...)` |
| graph_query | Neo4jGraphStore | `neo4j_store.py` | `.query(cypher=..., params=...)` |
| impact_analysis | Cypher BFS | `mcp_server.py` | 参考 `mcp_server.py:131-166` 的 Cypher BFS 实现 |
| get_context | Neo4jGraphStore | `neo4j_store.py` | `.get_node(node_id=...)` + `.get_relations(source_id=...)` |
| list_concepts | ConceptAligner | `aligner.py` | `.list_concepts()` |
| get_module_tree | ModuleClustering | `module_clustering.py` | `.get_module_tree()` |
| detect_changes | GitChangeDetector | `change_detector.py` | `.detect_changes(since=...)` |
| export_graph | Neo4jGraphStore | `neo4j_store.py` | `.query(cypher=...)` |

**共享辅助函数（`agent/_helpers.py`）：**
- `get_config()` → `LayerKGConfig.from_env()`
- `get_neo4j()` → `Neo4jGraphStore(uri, user, pwd)`
- `get_chroma()` → `ChromaStore(persist_dir, ollama_url, embedding_model)`
- `get_aligner()` → `ConceptAligner(chroma_store, neo4j_store)`
- `get_clustering()` → `ModuleClustering(neo4j_store)`
- `get_change_detector(repo_path)` → `GitChangeDetector(Path(repo_path))`

所有辅助函数使用 lazy init + 全局单例模式，避免重复创建连接。
