# Phase 4 Day 5: 联调问题修复清单

> 基于 curl 端到端测试发现的问题

## 联调结果

### ✅ 已验证通过
1. `/health` → `{"status":"ok"}`
2. `/api/graph/stats` → 382 节点，1131 边（Neo4j 连通正常）
3. `/api/graph?limit=3` → 节点+边数据正确
4. `/api/graph/node/{id}` → 节点详情、incoming/outgoing 关系正确
5. `/api/chat/stream` SSE → 流式对话完整，Agent 正确调用 graph_query 工具
6. `/api/trace/list` → 2 条 trace 记录
7. `/api/trace/thread/{id}` → 4 步 trace 详情（thinking→tool_call→tool_result→thinking）
8. `/api/trace/graph/mermaid` → Mermaid 图正确返回
9. Agent LLM（DeepSeek）连通，延迟约 3.4s 完整回答

---

## 🔴 Bug 1: tool_result 内容被 LangChain ToolMessage 包裹

### 现象
`graph.py` 的 `run_query_stream` 中 `on_tool_end` 事件：
```python
output = event["data"].get("output", "")
```
`output` 是 LangChain 的 `ToolMessage` 对象，`str(output)` 得到：
```
content='[{"type": "module", "count": 75}...]' name='graph_query' tool_call_id='call_00_...'
```
而不是纯 JSON。

### 修复
文件：`src/layerkg/agent/graph.py`

在 `on_tool_end` 分支中，提取 ToolMessage 的 content 属性而非 str()：
```python
elif kind == "on_tool_end":
    tool_name = event["name"]
    raw_output = event["data"].get("output", "")
    # 提取 ToolMessage 的 content 属性（可能是 str 或 ToolMessage 对象）
    if hasattr(raw_output, "content"):
        output_str = str(raw_output.content)
    else:
        output_str = str(raw_output)
    duration = None
    if tool_name in tool_start_time:
        duration = (time.time() - tool_start_time[tool_name]) * 1000
        del tool_start_time[tool_name]
    yield {
        "type": "tool_end",
        "tool": tool_name,
        "result": output_str[:500],
    }
    if trace_collector:
        await trace_collector.add_step(
            thread_id,
            type="tool_result",
            content=f"{tool_name} 返回结果",
            tool_name=tool_name,
            tool_result=output_str[:500],
            duration_ms=duration,
        )
```

---

## 🟡 Bug 2: 第一个 trace status=running 未正确关闭（低优先级）

### 现象
curl 截断导致 SSE 连接断开，`end_trace` 未被调用，trace 永远停留在 `running` 状态。

### 修复
这属于客户端断连的正常情况，添加超时自动清理即可。在 `TraceCollector._clean_old_traces_unlocked` 中已经有 max_age 清理（默认 3600s），无需额外处理。

如果需要更快清理，可以考虑：在 SSE 断连时（`asyncio.CancelledError`）触发 end_trace。

文件：`src/layerkg/web/router/chat.py` 的 `event_generator()`，添加 `finally` 清理：
```python
async def event_generator():
    trace_ended = False
    try:
        async with asyncio.timeout(120):
            async for event in run_query_stream(...):
                yield ServerSentEvent(...)
    except TimeoutError:
        ...
    except Exception as e:
        if collector:
            await collector.end_trace(thread_id, status="failed")
            trace_ended = True
        ...
    finally:
        # 客户端断连时确保 trace 关闭
        if collector and not trace_ended:
            try:
                trace = await collector.get_trace(thread_id)
                if trace and trace.status == "running":
                    await collector.end_trace(thread_id, status="completed")
            except Exception:
                pass
    yield ServerSentEvent(data=json.dumps({"thread_id": thread_id}), event="done")
```

---

## 验证步骤

修改后运行：
```bash
uv run pytest tests/ -q
```
然后重新启动后端：
```bash
uv run layerkg web --port 8000
```
测试：
```bash
curl -s -N -X POST http://localhost:8000/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "这个项目有多少个代码实体？"}'
```
验证 tool_end 事件中 result 是纯 JSON，不含 `content='...'` 包裹。
