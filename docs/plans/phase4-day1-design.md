# Phase 4 Day 1 方案设计：后端 API 骨架 + 流式对话

## 一、目标

搭建 FastAPI 后端骨架，实现对话 API（同步 + SSE 流式），新增 `run_query_stream` 流式变体，CLI 新增 `layerkg web` 命令。

**验收标准：** `curl` 测试 SSE 流式对话通过。

## 二、方案设计

### 2.1 新增依赖

```bash
uv add fastapi uvicorn sse-starlette
```

### 2.2 目录结构

```
src/layerkg/web/
├── __init__.py          # 空或导出 create_app
├── app.py               # FastAPI app 工厂
└── router/
    ├── __init__.py
    └── chat.py          # 对话 API（同步 + SSE）
```

### 2.3 app.py — FastAPI App 工厂

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from layerkg.web.router.chat import router as chat_router

def create_app() -> FastAPI:
    app = FastAPI(title="LayerKG Agent", version="0.1.0")
    
    # CORS（开发模式允许所有来源）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # 路由
    app.include_router(chat_router, prefix="/api")
    
    # 健康检查
    @app.get("/health")
    async def health():
        return {"status": "ok"}
    
    return app
```

### 2.4 router/chat.py — 对话 API

**同步接口：**
```python
class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None

class ChatResponse(BaseModel):
    answer: str
    thread_id: str
    duration_ms: int

@router.post("/chat", response_model=ChatResponse)
async def chat_sync(req: ChatRequest):
    start = time.time()
    thread_id = req.thread_id or str(uuid4())
    answer = await run_query(req.message, thread_id=thread_id)
    duration_ms = int((time.time() - start) * 1000)
    return ChatResponse(answer=answer, thread_id=thread_id, duration_ms=duration_ms)
```

**SSE 流式接口（POST）：**
```python
@router.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    thread_id = req.thread_id or str(uuid4())
    
    async def event_generator():
        try:
            async for event in run_query_stream(req.message, thread_id=thread_id):
                yield ServerSentEvent(data=json.dumps(event, ensure_ascii=False), event=event["type"])
        except TimeoutError:
            yield ServerSentEvent(data=json.dumps({"error": "Agent timeout"}), event="error")
        yield ServerSentEvent(data=json.dumps({"thread_id": thread_id}), event="done")
    
    return EventSourceResponse(event_generator())
```

**说明：** 用 POST 而非 GET，因为用户消息可能很长（GET URL 长度限制）。复用同一个 `ChatRequest` model。

### 2.5 agent/graph.py — 新增 run_query_stream

在现有 `run_query` 下方新增流式变体，**不改现有函数**：

```python
async def run_query_stream(question: str, thread_id: str | None = None) -> AsyncGenerator[dict, None]:
    """流式运行 Agent，yield 事件字典"""
    thread_id = thread_id or "default"
    agent = create_agent()
    config = _make_config(thread_id)
    
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
```

**关键点：**
- 复用 `create_agent()` 和 `_make_config()` — 不重复实现
- `astream_events(version="v2")` 是 LangGraph 原生支持的 API
- 只关注 3 种事件：token 流、工具开始、工具结束
- 添加 `asyncio.wait_for` 超时保护（120s），与 `run_query` 一致

### 2.6 cli.py — 新增 web 命令

在 `serve` 命令下方新增，参照 `serve` 的模式：

```python
@main.command()
@click.option("--host", default="0.0.0.0", help="监听地址")
@click.option("--port", default=8000, help="监听端口")
@click.option("--reload", is_flag=True, help="开发模式热重载")
def web(host: str, port: int, reload: bool):
    """启动 LayerKG Web API Server"""
    import uvicorn
    if reload:
        uvicorn.run("layerkg.web.app:create_app", host=host, port=port, reload=True, factory=True)
    else:
        from layerkg.web.app import create_app
        app = create_app()
        uvicorn.run(app, host=host, port=port)
```

**注意：** reload 模式用字符串路径 + `factory=True`，非 reload 模式直接传 app 实例。

### 2.7 SSE 心跳

审核建议 SSE 加心跳防超时。在 `event_generator()` 中每 15s 发送 ping：

```python
async def event_generator():
    last_ping = time.time()
    async for event in run_query_stream(message, thread_id=thread_id):
        yield ServerSentEvent(data=json.dumps(event, ensure_ascii=False), event=event["type"])
        last_ping = time.time()
    
    # Agent 完成后也检查是否需要 ping
    yield ServerSentEvent(data=json.dumps({"thread_id": thread_id}), event="done")
```

**说明：** SSE 心跳在反向代理场景重要，但当前直接暴露 uvicorn，暂不加复杂心跳逻辑。如果后续部署 Nginx 再补。

## 三、集成点

| 新增代码 | 调用的现有接口 | 文件 |
|---------|--------------|------|
| `router/chat.py` | `run_query(question, thread_id)` | `agent/graph.py` |
| `router/chat.py` | `run_query_stream(question, thread_id)` | `agent/graph.py`（新增） |
| `cli.py: web()` | `create_app()` | `web/app.py`（新增） |
| `app.py` | `CORSMiddleware` | `fastapi` |

## 四、不改什么

- ❌ 不改 `run_query` — 保持现有 CLI `ask` 命令正常工作
- ❌ 不改工具定义（`tools.py`）
- ❌ 不改 `_helpers.py`
- ❌ 不改 `prompt.py`
- ❌ 不改前端（Day 2 的事）
- ❌ 不做图谱 API（Day 3 的事）

## 五、风险

| 风险 | 缓解 |
|------|------|
| `astream_events` 与工具调用兼容性 | Day 1 第一件事先写 POC 验证 |
| SSE 连接超时 | FastAPI 默认无超时，uvicorn 有 30s idle；如遇问题加心跳 |
| Neo4j 连接池 | `_helpers.py` 已是单例模式，无需额外处理 |
