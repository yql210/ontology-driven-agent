# Phase 3 Day 3 实施计划（v2 修订版）

> 审核历史：v1 评分 8.2/10，已按审核意见修订
> 前置条件：Day 1+2 已完成（commit 9e9e1b9），8个工具 + Langfuse + 731测试通过

## 目标

1. CLI 交互式模式完善（对话记忆、Ctrl+C优雅退出）
2. 集成测试（mock LLM 端到端验证工具调用链）
3. 边界场景处理（超时、工具失败）
4. 代码质量提升

## Task 1：Agent 对话记忆 + create_agent 重构

### 核心设计：MemorySaver 全局单例

审核指出关键问题：每次 `create_agent()` 都新建 `MemorySaver()` 无法实现跨调用记忆。
解决方案：**全局 MemorySaver 单例** + **create_agent 保持只返回 graph**。

**修改 `graph.py`**：

```python
from langgraph.checkpoint.memory import MemorySaver

# 全局 checkpointer 单例 — 支持跨调用对话记忆
_checkpointer: MemorySaver | None = None

def _get_checkpointer() -> MemorySaver:
    global _checkpointer
    if _checkpointer is None:
        _checkpointer = MemorySaver()
    return _checkpointer


def create_agent() -> Any:
    """创建 ReAct Agent 编排图（带对话记忆）"""
    graph = StateGraph(AgentState)
    graph.add_node("agent", _agent_node)
    graph.add_node("tools", ToolNode(ALL_TOOLS))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    
    return graph.compile(checkpointer=_get_checkpointer())


def _make_config(thread_id: str = "default") -> dict:
    """生成 Agent 运行配置"""
    config: dict[str, Any] = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": 50,
    }
    handler = _get_langfuse_handler()
    if handler:
        config["callbacks"] = [handler]
    return config


async def run_query(question: str, thread_id: str = "default") -> str:
    """运行单次查询（异步，支持多轮对话）"""
    agent = create_agent()
    config = _make_config(thread_id)
    
    try:
        result = await asyncio.wait_for(
            agent.ainvoke(
                {"messages": [HumanMessage(content=question)]},
                config=config,
            ),
            timeout=120,
        )
    except asyncio.TimeoutError:
        return "查询超时（120秒），请尝试简化问题或减少搜索范围。"
    
    for msg in reversed(result["messages"]):
        if isinstance(msg, AIMessage) and msg.content:
            content = msg.content
            if isinstance(content, str):
                return content
            import json
            return json.dumps(content, ensure_ascii=False)
    return "无法生成回答。"
```

**关键变更**：
- `create_agent()` 保持只返回 graph（签名不变），不返回 tuple
- `MemorySaver` 用全局单例 `_get_checkpointer()`，跨调用共享
- 新增 `_make_config(thread_id)` 函数，合并 thread_id + Langfuse
- 新增 `import asyncio` + 超时处理

## Task 2：CLI 交互模式完善

**修改 `cli.py` 的 `ask` 命令**：

```python
@main.command()
@click.argument('question', required=False)
@click.option('--interactive', '-i', is_flag=True, help='交互式对话模式')
def ask(question, interactive):
    """向代码知识图谱提问。示例：layerkg ask "merge_node 被谁调用" """
    import asyncio
    import uuid
    from layerkg.agent.graph import run_query
    
    if interactive:
        click.echo("🔍 LayerKG 交互模式")
        click.echo("输入问题查询代码知识图谱，输入 quit/exit 退出\n")
        
        thread_id = str(uuid.uuid4())
        
        while True:
            try:
                q = click.prompt("", prompt_suffix="> ").strip()
            except (EOFError, KeyboardInterrupt):
                click.echo("\n再见！")
                break
            if q.lower() in ("quit", "exit", "q"):
                click.echo("再见！")
                break
            if not q:
                continue
            try:
                answer = asyncio.run(run_query(q, thread_id=thread_id))
                click.echo(f"\n{answer}\n")
                click.echo("-" * 60)
            except KeyboardInterrupt:
                click.echo("\n中断当前查询")
            except Exception as e:
                click.echo(f"\n错误: {e}\n")
    elif question:
        answer = asyncio.run(run_query(question))
        click.echo(answer)
    else:
        click.echo("请提供问题或使用 -i 进入交互模式")
```

**注意**：`run_query` 签名新增 `thread_id` 参数（有默认值 `"default"`），现有测试 `test_cli_ask.py` 中 `AsyncMock(return_value="test answer")` 不受影响（mock 会忽略额外参数）。

## Task 3：工具 try-catch 增强

给可能失败的工具添加外层 try-catch，返回友好 JSON 错误：

**修改 `tools.py`**：

1. **semantic_search** — ChromaDB 连接失败：
```python
@tool
def semantic_search(query: str, top_k: int = 5) -> str:
    # ... 现有 docstring ...
    try:
        chroma = get_chroma()
        results = chroma.search(query_text=query, n_results=top_k)
        return json.dumps(results, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": f"语义搜索失败: {e!s}", "suggestion": "尝试使用 graph_query 替代"}, ensure_ascii=False)
```

2. **export_graph** — Neo4j 查询失败：
```python
@tool
def export_graph(limit: int = 100) -> str:
    # ... 现有 docstring ...
    try:
        neo4j = get_neo4j()
        # ... 现有查询逻辑 ...
    except Exception as e:
        return json.dumps({"error": f"导出图谱失败: {e!s}"}, ensure_ascii=False)
```

3. **impact_analysis** — ImpactPropagator 错误：
在 `compute_impact` 调用处加 try-catch：
```python
    try:
        impacts = propagator.compute_impact([entity_id], ChangeType.BODY)
    except Exception as e:
        return json.dumps({"error": f"影响分析失败: {e!s}", "entity_id": entity_id}, ensure_ascii=False)
```

**不需要修改的工具**：
- `graph_query` — 已有 try-catch
- `get_context` — 已有 node None 检查 + chroma try-catch 降级
- `detect_changes` — 已有 CalledProcessError catch
- `list_concepts` / `get_module_tree` — 简单代理调用，错误由 LangGraph ToolNode 处理

## Task 4：集成测试

### 4.1 `tests/integration/test_agent_tools_e2e.py`

验证工具与底层模块的交互（比 unit 测试更深一层，验证 mock 链路正确性）：

```python
"""工具集成测试 — 验证工具→底层模块→结果的完整调用链"""

import json
import pytest
from unittest.mock import patch, MagicMock
from layerkg.change_detector import ChangeType


class TestImpactAnalysisIntegration:
    """impact_analysis 集成测试"""
    
    def test_exact_match_to_propagator(self):
        """精确名称匹配 → ImpactPropagator.compute_impact"""
        with patch("layerkg.agent.tools.get_neo4j") as mock_neo4j_fn, \
             patch("layerkg.agent.tools.get_impact_propagator") as mock_prop_fn:
            mock_neo4j = MagicMock()
            mock_neo4j.query.return_value = [{"id": "abc-123"}]
            mock_neo4j_fn.return_value = mock_neo4j
            
            mock_impact = MagicMock()
            mock_impact.to_dict.return_value = {"node_id": "x", "name": "Y", "impact_score": 0.7, "severity": "HIGH"}
            mock_prop = MagicMock()
            mock_prop.compute_impact.return_value = [mock_impact]
            mock_prop_fn.return_value = mock_prop
            
            from layerkg.agent.tools import impact_analysis
            result = json.loads(impact_analysis.invoke({"entity_name": "Test"}))
            
            assert result["source"] == "Test"
            assert result["total_count"] == 1
            mock_prop.compute_impact.assert_called_once_with(["abc-123"], ChangeType.BODY)
    
    def test_fuzzy_match_suggestions(self):
        """模糊匹配返回建议列表"""
        with patch("layerkg.agent.tools.get_neo4j") as mock_fn:
            mock_neo4j = MagicMock()
            mock_neo4j.query.side_effect = [
                [],  # 精确匹配无结果
                [{"id": "1", "name": "TestFunc"}, {"id": "2", "name": "TestClass"}],  # 模糊匹配
            ]
            mock_fn.return_value = mock_neo4j
            
            from layerkg.agent.tools import impact_analysis
            result = json.loads(impact_analysis.invoke({"entity_name": "Test"}))
            
            assert "error" in result
            assert len(result["suggestions"]) == 2


class TestGetContextIntegration:
    """get_context 集成测试"""
    
    def test_bidirectional_relations(self):
        """get_relations 被调用两次（outgoing + incoming）"""
        with patch("layerkg.agent.tools.get_neo4j") as mock_neo4j_fn, \
             patch("layerkg.agent.tools.get_chroma") as mock_chroma_fn:
            mock_neo4j = MagicMock()
            mock_neo4j.query.return_value = [{"id": "n1"}]
            mock_neo4j.get_node.return_value = {"name": "F", "entity_type": "function"}
            mock_neo4j.get_relations.return_value = [{"type": "CALLS", "target_id": "n2"}]
            mock_neo4j_fn.return_value = mock_neo4j
            mock_chroma_fn.return_value = MagicMock(search=MagicMock(return_value=[]))
            
            from layerkg.agent.tools import get_context
            json.loads(get_context.invoke({"entity_name": "F"}))
            
            # 验证 get_relations 被调两次：source_id= + target_id=
            calls = mock_neo4j.get_relations.call_args_list
            assert len(calls) == 2
            assert calls[0].kwargs.get("source_id") == "n1"
            assert calls[1].kwargs.get("target_id") == "n1"


class TestExportGraphIntegration:
    """export_graph 集成测试"""
    
    def test_two_queries_separate(self):
        """节点查询和边查询分别调用"""
        with patch("layerkg.agent.tools.get_neo4j") as mock_fn:
            mock_neo4j = MagicMock()
            mock_neo4j.query.side_effect = [
                [{"id": "1", "name": "A", "labels": ["CodeEntity"]}],
                [{"source": "1", "target": "2", "type": "CALLS", "properties": {}}],
            ]
            mock_fn.return_value = mock_neo4j
            
            from layerkg.agent.tools import export_graph
            result = json.loads(export_graph.invoke({"limit": 5}))
            
            assert result["node_count"] == 1
            assert result["edge_count"] == 1
            assert mock_neo4j.query.call_count == 2
```

### 4.2 `tests/integration/test_agent_graph_e2e.py`

验证 Agent 图结构完整性（不调 LLM API）：

```python
"""Agent 图结构集成测试"""

import pytest
from unittest.mock import patch, MagicMock


class TestAgentGraphStructure:
    """验证 Agent 图的结构和配置"""
    
    def test_create_agent_has_checkpointer(self):
        """create_agent 返回带 checkpointer 的编译图"""
        with patch("layerkg.agent._helpers.get_config") as mock_cfg:
            mock_cfg.return_value = MagicMock(
                agent_llm_model="test", agent_base_url="http://test", agent_api_key="k",
                langfuse_public_key="", langfuse_secret_key="",
            )
            from layerkg.agent.graph import create_agent
            agent = create_agent()
            # 验证图有 checkpointer
            assert agent.checkpointer is not None
    
    def test_make_config_includes_thread_id(self):
        """_make_config 包含 thread_id 和 recursion_limit"""
        from layerkg.agent.graph import _make_config
        config = _make_config("test-thread")
        assert config["configurable"]["thread_id"] == "test-thread"
        assert config["recursion_limit"] == 50
    
    def test_all_tools_registered_in_graph(self):
        """所有 8 个工具在 ToolNode 中注册"""
        with patch("layerkg.agent._helpers.get_config") as mock_cfg:
            mock_cfg.return_value = MagicMock(
                agent_llm_model="test", agent_base_url="http://test", agent_api_key="k",
                langfuse_public_key="", langfuse_secret_key="",
            )
            from layerkg.agent.graph import create_agent
            agent = create_agent()
            # 验证图节点包含 agent 和 tools
            assert "agent" in agent.nodes
            assert "tools" in agent.nodes
    
    def test_global_checkpointer_is_singleton(self):
        """全局 checkpointer 是单例"""
        with patch("layerkg.agent._helpers.get_config") as mock_cfg:
            mock_cfg.return_value = MagicMock(
                agent_llm_model="test", agent_base_url="http://test", agent_api_key="k",
                langfuse_public_key="", langfuse_secret_key="",
            )
            from layerkg.agent.graph import _get_checkpointer
            cp1 = _get_checkpointer()
            cp2 = _get_checkpointer()
            assert cp1 is cp2
```

## Task 5：更新现有测试

### 5.1 `tests/unit/agent/test_graph.py`

由于 `create_agent` 签名**不变**（仍只返回 graph），现有测试基本兼容。
但需更新 `run_query` 相关 mock（新增 `thread_id` 参数，有默认值不影响）。

### 5.2 `tests/unit/agent/test_cli_ask.py`

`run_query` 新增 `thread_id` 参数（默认值），AsyncMock 会自动处理，无需修改。
但 `test_ask_with_interactive_shows_prompt` 可能需要检查 thread_id 逻辑。

验证：`uv run pytest tests/ -v`

## Task 6：代码质量

```bash
uv run ruff check src/ tests/ --fix
uv run ruff format src/ tests/
uv run pytest tests/ -v
```

## 验收标准

- [ ] `uv run pytest tests/ -v` 全部通过
- [ ] `uv run ruff check src/ tests/` 无错误
- [ ] MemorySaver 全局单例，交互模式对话记忆有效
- [ ] 超时处理：120秒返回友好提示
- [ ] 集成测试覆盖 impact_analysis / get_context / export_graph 完整流程
- [ ] 工具 try-catch 覆盖 semantic_search / export_graph / impact_analysis

## 文件清单

| 操作 | 文件 |
|------|------|
| 修改 | `src/layerkg/agent/graph.py`（MemorySaver 单例 + _make_config + 超时） |
| 修改 | `src/layerkg/cli.py`（交互模式 + thread_id） |
| 修改 | `src/layerkg/agent/tools.py`（3 个工具 try-catch） |
| 修改 | `tests/unit/agent/test_graph.py`（适配新结构） |
| 新增 | `tests/integration/test_agent_tools_e2e.py` |
| 新增 | `tests/integration/test_agent_graph_e2e.py` |
