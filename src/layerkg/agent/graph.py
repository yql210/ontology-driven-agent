"""LangGraph ReAct Agent 状态图"""

from __future__ import annotations

from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, MessagesState, StateGraph
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
    return ChatAnthropic(  # type: ignore[call-arg]
        model=cfg.agent_llm_model,  # type: ignore[call-arg]
        base_url=cfg.agent_base_url,  # type: ignore[call-arg]
        api_key=cfg.agent_api_key,  # type: ignore[call-arg]
        timeout=60,  # type: ignore[call-arg]
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
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=question)]},
        config={"recursion_limit": 25},
    )
    # 取最后一条 AI 消息
    for msg in reversed(result["messages"]):
        if isinstance(msg, AIMessage) and msg.content:
            content = msg.content
            if isinstance(content, str):
                return content
            # 如果是 list 格式（多模态或工具调用结果），转 JSON
            import json
            return json.dumps(content, ensure_ascii=False)
    return "无法生成回答。"
