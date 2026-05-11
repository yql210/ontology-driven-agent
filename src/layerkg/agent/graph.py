"""LangGraph ReAct Agent 状态图"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from layerkg.agent.prompt import AGENT_SYSTEM_PROMPT
from layerkg.agent.tools import ALL_TOOLS

# 全局 checkpointer 单例 — 支持跨调用对话记忆
_checkpointer: MemorySaver | None = None


def _get_checkpointer() -> MemorySaver:
    """获取全局 MemorySaver 单例"""
    global _checkpointer
    if _checkpointer is None:
        _checkpointer = MemorySaver()
    return _checkpointer


class AgentState(MessagesState):
    """ReAct Agent 状态 — 只需 messages"""

    pass


def _create_llm() -> ChatOpenAI:
    """创建 LLM 实例（智谱 OpenAI 兼容接口）"""
    from layerkg.agent._helpers import get_config

    cfg = get_config()
    return ChatOpenAI(
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


def _get_langfuse_handler():
    """创建 Langfuse 回调 handler（可选，无 key 时返回 None）"""
    from layerkg.agent._helpers import get_config

    cfg = get_config()
    if not cfg.langfuse_public_key or not cfg.langfuse_secret_key:
        return None
    from langfuse.callback import CallbackHandler

    return CallbackHandler(
        public_key=cfg.langfuse_public_key,
        secret_key=cfg.langfuse_secret_key,
        host=cfg.langfuse_host,
    )


def create_agent() -> Any:
    """创建 ReAct Agent 编排图（带对话记忆）"""
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

    return graph.compile(checkpointer=_get_checkpointer())


def _make_config(thread_id: str = "default") -> dict[str, Any]:
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
    except TimeoutError:
        return "查询超时（120秒），请尝试简化问题或减少搜索范围。"

    for msg in reversed(result["messages"]):
        if isinstance(msg, AIMessage) and msg.content:
            content = msg.content
            if isinstance(content, str):
                return content
            import json

            return json.dumps(content, ensure_ascii=False)
    return "无法生成回答。"


async def run_query_stream(question: str, thread_id: str | None = None) -> AsyncGenerator[dict]:
    """流式运行 Agent，yield 事件字典。"""
    thread_id = thread_id or "default"
    agent = create_agent()
    config = _make_config(thread_id)

    try:
        async for event in agent.astream_events(
            {"messages": [HumanMessage(content=question)]},
            config=config,
            version="v2",
        ):
            kind = event["event"]
            if kind == "on_chat_model_stream":
                chunk = event["data"]["chunk"]
                if hasattr(chunk, "content") and chunk.content:
                    yield {"type": "token", "content": chunk.content}
            elif kind == "on_tool_start":
                yield {"type": "tool_start", "tool": event["name"], "args": event["data"].get("input", {})}
            elif kind == "on_tool_end":
                yield {"type": "tool_end", "tool": event["name"]}
    except Exception as e:
        yield {"type": "error", "message": str(e)}
