"""LangGraph ReAct Agent 状态图"""

from __future__ import annotations

import asyncio
import time
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

# 全局 LLM 单例 — 避免重复创建连接
_llm: ChatOpenAI | None = None


def _get_checkpointer() -> MemorySaver:
    """获取全局 MemorySaver 单例"""
    global _checkpointer
    if _checkpointer is None:
        _checkpointer = MemorySaver()
    return _checkpointer


def _get_llm() -> ChatOpenAI:
    """获取全局 LLM 单例"""
    global _llm
    if _llm is None:
        _llm = _create_llm()
    return _llm


def _reset_llm() -> None:
    """重置全局 LLM（测试用）"""
    global _llm
    _llm = None


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
        timeout=180,
        max_retries=3,
    )


async def _agent_node(state: AgentState) -> dict[str, list[BaseMessage]]:
    """LLM 推理节点"""
    llm = _get_llm()
    llm_with_tools = llm.bind_tools(ALL_TOOLS)
    messages: list[BaseMessage] = [
        SystemMessage(content=AGENT_SYSTEM_PROMPT),
        *state["messages"],
    ]
    response = await llm_with_tools.ainvoke(messages)
    return {"messages": [response]}


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
    return {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": 15,  # 防止死循环（agent→tools→agent 最多 7 轮）
    }


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
            timeout=180,
        )
    except TimeoutError:
        return "查询超时（180秒），请尝试简化问题或减少搜索范围。"
    except Exception as e:
        err_name = type(e).__name__
        err_msg = str(e)
        # 清理 checkpointer 中不完整的消息序列
        try:
            state = await agent.aget_state(config)
            messages = state.values.get("messages", [])
            if messages and isinstance(messages[-1], AIMessage) and getattr(messages[-1], "tool_calls", None):
                await agent.update_state(config, {"messages": messages[:-1]}, as_node="agent")
        except Exception:
            pass
        if "Recursion" in err_name:
            # 从 checkpointer 中提取最后一条 AI 消息
            try:
                state = await agent.aget_state(config)
                for msg in reversed(state.values.get("messages", [])):
                    if isinstance(msg, AIMessage) and msg.content:
                        content = msg.content
                        if isinstance(content, str) and len(content) > 10:
                            return content
            except Exception:
                pass
            return "Agent 工具调用次数超限，可能是因为部分数据未构建（如概念实体、模块聚类）。请使用更具体的代码实体名称提问，例如「Cache 类有哪些方法？」。"
        if "Unexpected end of JSON" in err_msg or "ConnectionError" in err_msg:
            return "LLM 服务连接中断，请稍后重试。"
        if "400" in err_msg and "tool_calls" in err_msg:
            return "上一轮对话因网络中断导致状态不一致，已自动修复，请重新提问。"
        return f"查询出错: {err_msg}"

    for msg in reversed(result["messages"]):
        if isinstance(msg, AIMessage) and msg.content:
            content = msg.content
            if isinstance(content, str):
                return content
            import json

            return json.dumps(content, ensure_ascii=False)
    return "无法生成回答。"


async def run_query_stream(
    question: str,
    thread_id: str | None = None,
    trace_collector: Any | None = None,
) -> AsyncGenerator[dict]:
    """流式运行 Agent，yield 事件字典。

    Args:
        question: 用户问题。
        thread_id: 会话线程 ID。
        trace_collector: TraceCollector 实例（可选）。

    Yields:
        事件字典: {"type": "token"|"tool_start"|"tool_end"|"error", ...}
    """
    thread_id = thread_id or "default"
    agent = create_agent()
    config = _make_config(thread_id)

    # 开始 trace
    if trace_collector:
        await trace_collector.start_trace(thread_id, question)

    try:
        tool_start_time: dict[str, float] = {}

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
            elif kind == "on_chat_model_end":
                output = event["data"].get("output", {})
                content = ""
                if hasattr(output, "content"):
                    content = str(output.content)[:200]
                if trace_collector and content:
                    await trace_collector.add_step(
                        thread_id, type="thinking", content=content
                    )
            elif kind == "on_tool_start":
                tool_name = event["name"]
                args = event["data"].get("input", {})
                tool_start_time[tool_name] = time.time()
                yield {"type": "tool_start", "tool": tool_name, "args": args}
                if trace_collector:
                    await trace_collector.add_step(
                        thread_id,
                        type="tool_call",
                        content=f"调用 {tool_name}",
                        tool_name=tool_name,
                        tool_args=args,
                    )
            elif kind == "on_tool_end":
                tool_name = event["name"]
                raw_output = event["data"].get("output", "")
                # Extract pure content from ToolMessage objects
                output_str = (
                    str(raw_output.content)
                    if hasattr(raw_output, "content")
                    else str(raw_output)
                )
                duration = None
                if tool_name in tool_start_time:
                    duration = (time.time() - tool_start_time[tool_name]) * 1000
                    del tool_start_time[tool_name]
                yield {
                    "type": "tool_end",
                    "tool": tool_name,
                    "result": output_str,
                }
                if trace_collector:
                    await trace_collector.add_step(
                        thread_id,
                        type="tool_result",
                        content=f"{tool_name} 返回结果",
                        tool_name=tool_name,
                        tool_result=output_str,
                        duration_ms=duration,
                    )
    except Exception as e:
        if trace_collector:
            await trace_collector.end_trace(thread_id, status="failed")
        # 清理 checkpointer 中不完整的消息序列（避免后续 tool_calls 缺 ToolMessage 的 400 错误）
        try:
            state = await agent.aget_state(config)
            messages = state.values.get("messages", [])
            # 如果最后一条是 AIMessage 带 tool_calls，说明 tool 还没执行就被中断了
            if messages and isinstance(messages[-1], AIMessage) and getattr(messages[-1], "tool_calls", None):
                # 回退到最近一条 HumanMessage 之前的状态
                await agent.update_state(config, {"messages": messages[:-1]}, as_node="agent")
        except Exception:
            pass
        # 友好错误消息
        err_msg = str(e)
        if "Unexpected end of JSON" in err_msg or "ConnectionError" in err_msg:
            friendly = "LLM 服务连接中断，请稍后重试。"
        elif "400" in err_msg and "tool_calls" in err_msg:
            friendly = "上一轮对话因网络中断导致状态不一致，已自动修复，请重新提问。"
        else:
            friendly = f"查询出错: {err_msg}"
        yield {"type": "error", "message": friendly}
        return

    # 正常结束
    if trace_collector:
        await trace_collector.end_trace(thread_id, status="completed")
