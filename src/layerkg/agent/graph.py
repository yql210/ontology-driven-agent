"""LangGraph ReAct Agent 状态图"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from layerkg.agent.prompt import AGENT_SYSTEM_PROMPT
from layerkg.agent.tools import ALL_TOOLS


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

    return graph.compile()


async def run_query(question: str) -> str:
    """运行单次查询（异步）"""
    agent = create_agent()
    config = {"recursion_limit": 50}
    handler = _get_langfuse_handler()
    if handler:
        config["callbacks"] = [handler]

    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=question)]},
        config=config,
    )
    # 取最后一条 AI 消息
    for msg in reversed(result["messages"]):
        if isinstance(msg, AIMessage) and msg.content:
            content = msg.content
            if isinstance(content, str):
                return content
            import json

            return json.dumps(content, ensure_ascii=False)
    return "无法生成回答。"
