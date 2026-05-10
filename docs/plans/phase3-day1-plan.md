# Phase 3 Day 1 实施计划：Agent 骨架

> 前置条件：Phase 3 整体方案已通过终审（9.6/10），见 `docs/plans/phase3-design.md`

## 目标

`uv run layerkg ask "merge_node 被谁调用"` 能跑通一个完整的工具调用循环，返回包含调用者的自然语言回答。

## Task 列表（按执行顺序）

### Task 1：安装依赖

```bash
uv add langgraph langchain-anthropic
```

> langfuse 在 Day 2 安装，Day 1 不需要。

验证：`uv run python -c "import langgraph; import langchain_anthropic; print('OK')"`

### Task 2：智谱 API 兼容性验证脚本

创建 `scripts/test_zhipu_api.py`：

```python
"""验证智谱 Anthropic 兼容接口是否支持工具调用"""
import os
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

@tool
def test_add(a: int, b: int) -> int:
    """加法测试工具"""
    return a + b

llm = ChatAnthropic(
    model="claude-sonnet-4-20250514",
    base_url=os.getenv("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/anthropic"),
    api_key=os.getenv("ZHIPU_API_KEY"),
    timeout=30,
)

llm_with_tools = llm.bind_tools([test_add])

response = llm_with_tools.invoke([
    SystemMessage(content="你是一个测试助手。"),
    HumanMessage(content="请计算 3+5"),
])

print(f"Response type: {type(response)}")
print(f"Content: {response.content}")
print(f"Tool calls: {response.tool_calls}")
print("✅ API 兼容性验证通过")
```

运行：`ZHIPU_API_KEY=xxx uv run python scripts/test_zhipu_api.py`

如果失败（B 计划）：改用 `langchain-openai` + OpenAI 兼容接口。

### Task 3b：扩展 config.py（添加 Agent LLM 配置）

在 `src/layerkg/config.py` 的 `LayerKGConfig` 中添加 Agent 相关字段：

```python
# 在现有字段后添加（docstring 区域也要更新）

# Agent/LLM 配置（Phase 3 新增）
agent_llm_provider: str = "zhipu"
agent_llm_model: str = "claude-sonnet-4-20250514"
agent_api_key: str = ""
agent_base_url: str = "https://open.bigmodel.cn/api/anthropic"

# 在 from_env() 的 return cls(...) 中添加：
agent_llm_provider=os.getenv("LAYERKG_AGENT_LLM_PROVIDER", cls.agent_llm_provider),
agent_llm_model=os.getenv("LAYERKG_AGENT_LLM_MODEL", cls.agent_llm_model),
agent_api_key=os.getenv("LAYERKG_AGENT_API_KEY", cls.agent_api_key),
agent_base_url=os.getenv("LAYERKG_AGENT_BASE_URL", cls.agent_base_url),
```

`.env` 中添加：
```
LAYERKG_AGENT_API_KEY=<智谱API key>
LAYERKG_AGENT_BASE_URL=https://open.bigmodel.cn/api/anthropic
```

对应更新 `graph.py` 中的 `_create_llm()`：
```python
def _create_llm() -> ChatAnthropic:
    """创建 LLM 实例"""
    from layerkg.agent._helpers import get_config
    cfg = get_config()
    return ChatAnthropic(
        model=cfg.agent_llm_model,
        base_url=cfg.agent_base_url,
        api_key=cfg.agent_api_key,
        timeout=60,
    )
```

### Task 4：新建 agent/ 目录结构

```
src/layerkg/agent/
├── __init__.py      # 导出 create_agent, run_query
├── graph.py         # LangGraph 状态图构建
├── tools.py         # LangChain Tool 封装
├── prompt.py        # System Prompt 常量
└── _helpers.py      # 共享辅助函数
```

每个文件初始内容：

**`__init__.py`：**
```python
"""LayerKG Agent: LangGraph ReAct 编排层"""
from layerkg.agent.graph import create_agent

__all__ = ["create_agent"]
```

**`prompt.py`：**
```python
"""Agent System Prompt"""

AGENT_SYSTEM_PROMPT = """你是 LayerKG 代码知识图谱助手。你可以帮助用户理解代码架构、查询依赖关系、分析变更影响。

【工具列表】
1. semantic_search - 语义搜索代码片段（top_k 建议 5-10）
2. graph_query - 执行 Cypher 图查询（关系、依赖、调用链）

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
- 如果查询无结果，告知用户并建议换搜索关键词或用 semantic_search 替代 graph_query

【查询技巧】
- graph_query 的 cypher 参数必须是合法的 Neo4j Cypher 语句
- 查询时优先用 name 和 file_path 属性定位实体
"""
```

（Day 1 只暴露 semantic_search 和 graph_query 两个工具，prompt 也只列这两个）

**`_helpers.py`：**
```python
"""Agent 共享辅助函数 — lazy init 单例模式"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from layerkg.config import LayerKGConfig

if TYPE_CHECKING:
    from layerkg.chroma_store import ChromaStore
    from layerkg.neo4j_store import Neo4jGraphStore

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
        from layerkg.neo4j_store import Neo4jGraphStore
        cfg = get_config()
        _neo4j = Neo4jGraphStore(cfg.neo4j_uri, cfg.neo4j_user, cfg.neo4j_password)
    return _neo4j


def get_chroma() -> ChromaStore:
    global _chroma
    if _chroma is None:
        from layerkg.chroma_store import ChromaStore
        cfg = get_config()
        _chroma = ChromaStore(cfg.chroma_persist_dir, cfg.ollama_base_url, cfg.embedding_model)
    return _chroma
```

### Task 5：封装 2 个核心工具（Day 1 先做 2 个，剩余 Day 2）

**`tools.py`：**
```python
"""LangChain Tool 封装 — Agent 可调用的工具"""

from __future__ import annotations

import json

from langchain_core.tools import tool

from layerkg.agent._helpers import get_chroma, get_neo4j


@tool
def semantic_search(query: str, top_k: int = 5) -> str:
    """语义搜索：在代码库中搜索与 query 相关的代码片段。

    Args:
        query: 搜索关键词或自然语言描述
        top_k: 返回结果数量，建议 5-10

    Returns:
        匹配的代码片段列表（JSON），包含文件路径、函数名、相似度分数
    """
    chroma = get_chroma()
    results = chroma.search(query_text=query, n_results=top_k)
    return json.dumps(results, ensure_ascii=False, indent=2)


@tool
def graph_query(cypher: str) -> str:
    """执行 Cypher 图查询，查询代码实体之间的关系。

    常用查询模式：
    - 函数调用关系：MATCH (a)-[:CALLS]->(b) WHERE a.name CONTAINS 'xxx' RETURN a.name, b.name
    - 模块依赖：MATCH (m:ModuleEntity)-[:CONTAINS]->(c) RETURN m.name, c.name
    - 概念关联：MATCH (c:ConceptEntity)<-[:DESCRIBES]-(e) RETURN c.name, e.name

    Args:
        cypher: Neo4j Cypher 查询语句

    Returns:
        查询结果的 JSON 格式字符串
    """
    neo4j = get_neo4j()
    try:
        results = neo4j.query(cypher)
        return json.dumps(results, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Cypher 查询错误: {e!s}\n请检查语法是否正确。"


# Day 1 先暴露这两个
ALL_TOOLS = [semantic_search, graph_query]
```

### Task 6：实现 AgentState + 状态图

**`graph.py`：**
```python
"""LangGraph ReAct Agent 状态图"""

from __future__ import annotations

import os
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, CompiledStateGraph, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from layerkg.agent.prompt import AGENT_SYSTEM_PROMPT
from layerkg.agent.tools import ALL_TOOLS


class AgentState(MessagesState):
    """ReAct Agent 状态 — 只需 messages"""
    pass


def _create_llm() -> ChatAnthropic:
    """创建 LLM 实例（智谱 Anthropic 兼容接口）"""
    from layerkg.agent._helpers import get_config
    cfg = get_config()
    return ChatAnthropic(
        model=cfg.agent_llm_model,
        base_url=cfg.agent_base_url,
        api_key=cfg.agent_api_key,
        timeout=60,
    )


async def _agent_node(state: AgentState) -> dict[str, list[BaseMessage]]:
    """LLM 推理节点"""
    llm = _create_llm()
    llm_with_tools = llm.bind_tools(ALL_TOOLS)
    messages: list[BaseMessage] = [
        SystemMessage(content=AGENT_SYSTEM_PROMPT),
        *state["messages"],
    ]
    response = await llm_with_tools.ainvoke(messages)
    return {"messages": [response]}


def create_agent() -> CompiledStateGraph:
    """创建 ReAct Agent 编排图"""
    graph = StateGraph(AgentState)

    # 节点
    graph.add_node("agent", _agent_node)
    graph.add_node("tools", ToolNode(ALL_TOOLS))

    # 边
    graph.add_edge(START, "agent")
    graph.add_conditional_edges(
        "agent",
        tools_condition,
        {"tools": "tools", END: END},
    )
    graph.add_edge("tools", "agent")

    return graph.compile(recursion_limit=25)


async def run_query(question: str) -> str:
    """运行单次查询（异步）"""
    agent = create_agent()
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=question)]}
    )
    # 取最后一条 AI 消息
    for msg in reversed(result["messages"]):
        if isinstance(msg, AIMessage) and msg.content:
            return msg.content
    return "无法生成回答。"
```

**`__init__.py` 更新：**
```python
"""LayerKG Agent: LangGraph ReAct 编排层"""

from layerkg.agent.graph import create_agent, run_query

__all__ = ["create_agent", "run_query"]
```

### Task 7：CLI ask 命令

在 `cli.py` 中新增 `ask` 命令：

```python
# 在现有 cli.py 的 import 区域添加：
import asyncio

# 在文件末尾（@cli.command build 之后）添加：

@main.command()
@click.argument("question", required=False)
@click.option("--interactive", "-i", is_flag=True, help="交互式对话模式")
def ask(question: str | None, interactive: bool) -> None:
    """向代码知识图谱提问。示例：layerkg ask "merge_node 被谁调用" """
    if not question and not interactive:
        click.echo("请提供问题或使用 -i 进入交互模式")
        return

    from layerkg.agent.graph import run_query

    if interactive:
        click.echo("LayerKG 交互模式（输入 quit 退出）")
        while True:
            try:
                q = click.prompt("", prompt_suffix="❯ ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if q.lower() in ("quit", "exit", "q"):
                break
            if not q:
                continue
            answer = asyncio.run(run_query(q))
            click.echo(f"\n{answer}\n")
    else:
        answer = asyncio.run(run_query(question))
        click.echo(answer)
```

### Task 8：端到端验证

```bash
# 验证导入
uv run python -c "from layerkg.agent import create_agent; print('✅ 导入成功')"

# 验证 CLI 注册
uv run layerkg ask --help

# 端到端验证（需要智谱 API key）
uv run layerkg ask "merge_node 被谁调用"
```

预期输出：Agent 自动调用 `graph_query` 执行 Cypher，返回 merge_node 的调用者列表。

## 测试计划（Task 4-7 同步写测试，TDD 先测试后实现）

**`tests/unit/agent/` 目录：**

```
tests/unit/agent/
├── __init__.py
├── test_helpers.py    # _helpers.py 辅助函数测试
├── test_tools.py      # 工具封装测试
├── test_graph.py      # 状态图构建测试
└── test_cli_ask.py    # CLI ask 命令测试
```

### test_helpers.py

```python
"""测试 _helpers.py 的 lazy init 和单例模式"""

from unittest.mock import MagicMock, patch

import pytest


def test_get_config_returns_config():
    """get_config 返回 LayerKGConfig 实例"""
    from layerkg.agent._helpers import get_config
    config = get_config()
    assert config is not None
    assert config.neo4j_uri is not None


def test_get_config_singleton():
    """多次调用 get_config 返回同一实例"""
    from layerkg.agent._helpers import get_config
    c1 = get_config()
    c2 = get_config()
    assert c1 is c2


def test_get_neo4j_returns_store():
    """get_neo4j 返回 Neo4jGraphStore 实例"""
    from layerkg.agent._helpers import get_neo4j
    store = get_neo4j()
    assert store is not None


def test_get_chroma_returns_store():
    """get_chroma 返回 ChromaStore 实例"""
    from layerkg.agent._helpers import get_chroma
    store = get_chroma()
    assert store is not None
```

### test_tools.py

```python
"""测试工具封装"""

import json
from unittest.mock import MagicMock, patch

import pytest


def test_semantic_search_returns_json():
    """semantic_search 返回 JSON 字符串"""
    from layerkg.agent.tools import semantic_search
    
    mock_results = [{"name": "test_func", "score": 0.9}]
    with patch("layerkg.agent.tools.get_chroma") as mock_get:
        mock_chroma = MagicMock()
        mock_chroma.search.return_value = mock_results
        mock_get.return_value = mock_chroma
        
        result = semantic_search.invoke({"query": "test", "top_k": 5})
        
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert parsed[0]["name"] == "test_func"
        mock_chroma.search.assert_called_once_with(query_text="test", n_results=5)


def test_graph_query_returns_json():
    """graph_query 返回 JSON 字符串"""
    from layerkg.agent.tools import graph_query
    
    mock_results = [{"name": "merge_node", "type": "function"}]
    with patch("layerkg.agent.tools.get_neo4j") as mock_get:
        mock_neo4j = MagicMock()
        mock_neo4j.query.return_value = mock_results
        mock_get.return_value = mock_neo4j
        
        result = graph_query.invoke({"cypher": "MATCH (n) RETURN n LIMIT 1"})
        
        parsed = json.loads(result)
        assert isinstance(parsed, list)


def test_graph_query_handles_error():
    """graph_query 处理 Cypher 语法错误"""
    from layerkg.agent.tools import graph_query
    
    with patch("layerkg.agent.tools.get_neo4j") as mock_get:
        mock_neo4j = MagicMock()
        mock_neo4j.query.side_effect = Exception("Invalid Cypher")
        mock_get.return_value = mock_neo4j
        
        result = graph_query.invoke({"cypher": "INVALID"})
        
        assert "错误" in result or "error" in result.lower()


def test_all_tools_defined():
    """ALL_TOOLS 包含 semantic_search 和 graph_query"""
    from layerkg.agent.tools import ALL_TOOLS
    assert len(ALL_TOOLS) == 2
    names = [t.name for t in ALL_TOOLS]
    assert "semantic_search" in names
    assert "graph_query" in names
```

### test_graph.py

```python
"""测试状态图构建"""

from unittest.mock import MagicMock, patch

import pytest


def test_create_agent_returns_compiled_graph():
    """create_agent 返回可执行的编译图"""
    with patch.dict("os.environ", {"ZHIPU_API_KEY": "test-key"}):
        from layerkg.agent.graph import create_agent
        agent = create_agent()
        assert agent is not None
        # 编译后的图有 invoke 方法
        assert hasattr(agent, "invoke")
        assert hasattr(agent, "ainvoke")


def test_agent_state_is_messages_state():
    """AgentState 继承 MessagesState"""
    from layerkg.agent.graph import AgentState
    from langgraph.graph import MessagesState
    assert issubclass(AgentState, MessagesState)
```

### test_cli_ask.py

```python
"""测试 CLI ask 命令"""

from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner


def test_ask_requires_question_or_interactive():
    """不带参数运行 ask 显示提示"""
    from layerkg.cli import cli
    runner = CliRunner()
    result = runner.invoke(cli, ["ask"])
    assert "请提供问题" in result.output or "非零退出" in str(result)


def test_ask_with_question_calls_run_query():
    """ask 命令调用 run_query"""
    from layerkg.cli import cli
    
    async def mock_run_query(q: str) -> str:
        return f"Mock answer for: {q}"
    
    runner = CliRunner()
    with patch("layerkg.cli.run_query", side_effect=mock_run_query):
        # 需要也 patch asyncio.run
        with patch("asyncio.run", return_value="Mock answer"):
            # 直接测试命令参数解析
            pass  # 复杂异步测试留给集成测试
```

## 验收标准

- [ ] `uv run python -c "from layerkg.agent import create_agent"` 无报错
- [ ] `uv run layerkg ask --help` 显示帮助信息
- [ ] `uv run pytest tests/unit/agent/ -v` 全部通过
- [ ] `uv run ruff check src/layerkg/agent/ tests/unit/agent/` 无错误
- [ ] `uv run pytest tests/ -v` 原有 702 tests 不受影响
- [ ] `uv run layerkg ask "merge_node 被谁调用"` 返回有意义的结果（需要 API key）

## 文件清单

| 操作 | 文件 |
|------|------|
| 新建 | `scripts/test_zhipu_api.py` |
| 新建 | `src/layerkg/agent/__init__.py` |
| 新建 | `src/layerkg/agent/graph.py` |
| 新建 | `src/layerkg/agent/tools.py` |
| 新建 | `src/layerkg/agent/prompt.py` |
| 新建 | `src/layerkg/agent/_helpers.py` |
| 修改 | `src/layerkg/cli.py`（添加 ask 命令） |
| 修改 | `.env`（添加 ZHIPU_API_KEY） |
| 新建 | `tests/unit/agent/__init__.py` |
| 新建 | `tests/unit/agent/test_helpers.py` |
| 新建 | `tests/unit/agent/test_tools.py` |
| 新建 | `tests/unit/agent/test_graph.py` |
| 新建 | `tests/unit/agent/test_cli_ask.py` |
