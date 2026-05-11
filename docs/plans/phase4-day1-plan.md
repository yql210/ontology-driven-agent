# Phase 4 Day 1 实施计划

## Task 1：安装依赖

```bash
cd /opt/data/workspace/ontology-driven-agent
uv add fastapi uvicorn sse-starlette
```

验证：
```bash
uv run python -c "import fastapi; print(fastapi.__version__)"
uv run python -c "import uvicorn; print(uvicorn.__version__)"
```

## Task 2：创建 web/ 目录结构

创建以下文件（空文件即可）：
```
src/layerkg/web/__init__.py
src/layerkg/web/app.py
src/layerkg/web/router/__init__.py
src/layerkg/web/router/chat.py
```

## Task 3：实现 app.py — FastAPI App 工厂

文件：`src/layerkg/web/app.py`

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from layerkg.web.router.chat import router as chat_router


def create_app() -> FastAPI:
    app = FastAPI(title="LayerKG Agent", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(chat_router, prefix="/api")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
```

## Task 4：实现 router/chat.py — 同步对话 API

文件：`src/layerkg/web/router/chat.py`

```python
import json
import time
from uuid import uuid4

from fastapi import APIRouter
from pydantic import BaseModel, field_validator
from layerkg.agent.graph import run_query

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None

    @field_validator("message")
    @classmethod
    def message_not_empty(cls, v: str) -> str:
        if not v.strip():
            msg = "message cannot be empty"
            raise ValueError(msg)
        return v[:2000]


class ChatResponse(BaseModel):
    answer: str
    thread_id: str
    duration_ms: int


@router.post("/chat", response_model=ChatResponse)
async def chat_sync(req: ChatRequest) -> ChatResponse:
    start = time.time()
    thread_id = req.thread_id or str(uuid4())
    answer = await run_query(req.message, thread_id=thread_id)
    duration_ms = int((time.time() - start) * 1000)
    return ChatResponse(answer=answer, thread_id=thread_id, duration_ms=duration_ms)
```

## Task 5：实现 run_query_stream — 流式变体

文件：`src/layerkg/agent/graph.py`（在现有 `run_query` 下方新增）

**注意：** `HumanMessage` 已在 graph.py 顶部导入（`from langchain_core.messages import HumanMessage, ...`），无需额外导入。

```python
async def run_query_stream(question: str, thread_id: str | None = None) -> AsyncGenerator[dict, None]:
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
```

**说明：** 在 async generator 内部 try/except 捕获异常，yield error 事件，确保调用方总能收到结束信号。

## Task 6：实现 SSE 流式接口

在 `router/chat.py` 中新增：

```python
import asyncio
from sse_starlette import EventSourceResponse, ServerSentEvent

@router.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    from layerkg.agent.graph import run_query_stream

    thread_id = req.thread_id or str(uuid4())

    async def event_generator():
        try:
            async with asyncio.timeout(120):
                async for event in run_query_stream(req.message, thread_id=thread_id):
                    yield ServerSentEvent(
                        data=json.dumps(event, ensure_ascii=False),
                        event=event["type"],
                    )
        except TimeoutError:
            yield ServerSentEvent(
                data=json.dumps({"error": "Agent timeout"}),
                event="error",
            )
        except Exception as e:
            yield ServerSentEvent(
                data=json.dumps({"error": str(e)}),
                event="error",
            )
        # done 事件始终发送（包括异常情况），确保 thread_id 返回给前端
        yield ServerSentEvent(
            data=json.dumps({"thread_id": thread_id}),
            event="done",
        )

    return EventSourceResponse(event_generator())
```

**说明：** 
- `done` 事件在 try/except 外部，确保始终发送
- `asyncio.timeout(120)` 做 120s 超时保护
- `ServerSentEvent` 使用 `data` + `event` 两个参数（sse-starlette 标准用法）

## Task 7：CLI web 命令

在 `cli.py` 的 `serve` 命令下方新增：

```python
@main.command()
@click.option("--host", default="0.0.0.0", help="监听地址")
@click.option("--port", default=8000, help="监听端口")
@click.option("--reload", is_flag=True, help="开发模式热重载")
def web(host: str, port: int, reload: bool):
    """启动 LayerKG Web API Server"""
    import uvicorn

    if reload:
        uvicorn.run(
            "layerkg.web.app:create_app",
            host=host,
            port=port,
            reload=True,
            factory=True,
        )
    else:
        from layerkg.web.app import create_app

        app = create_app()
        uvicorn.run(app, host=host, port=port)
```

## Task 8：单元测试

文件：`tests/unit/test_web.py`

```python
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from layerkg.web.app import create_app


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


class TestHealth:
    def test_health_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestChatSync:
    @patch("layerkg.web.router.chat.run_query", new_callable=AsyncMock)
    def test_chat_sync_returns_answer(self, mock_run, client):
        mock_run.return_value = "ConceptAligner 在 aligner.py"
        resp = client.post("/api/chat", json={"message": "ConceptAligner在哪"})
        assert resp.status_code == 200
        data = resp.json()
        assert "ConceptAligner" in data["answer"]
        assert data["thread_id"]
        assert data["duration_ms"] >= 0

    def test_chat_sync_empty_message_rejected(self, client):
        resp = client.post("/api/chat", json={"message": "  "})
        assert resp.status_code == 422

    @patch("layerkg.web.router.chat.run_query", new_callable=AsyncMock)
    def test_chat_sync_with_thread_id(self, mock_run, client):
        mock_run.return_value = "ok"
        resp = client.post("/api/chat", json={"message": "test", "thread_id": "my-thread"})
        assert resp.status_code == 200
        assert resp.json()["thread_id"] == "my-thread"


class TestChatStream:
    def test_chat_stream_returns_events(self, client):
        async def fake_stream(*args, **kwargs):
            yield {"type": "token", "content": "Hello"}
            yield {"type": "tool_start", "tool": "graph_query", "args": {}}
            yield {"type": "tool_end", "tool": "graph_query"}
            yield {"type": "token", "content": " World"}

        with patch("layerkg.web.router.chat.run_query_stream", return_value=fake_stream()):
            resp = client.post("/api/chat/stream", json={"message": "test"})
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")

    def test_chat_stream_empty_message_rejected(self, client):
        resp = client.post("/api/chat/stream", json={"message": ""})
        assert resp.status_code == 422

    def test_chat_stream_with_thread_id(self, client):
        async def fake_stream(*args, **kwargs):
            yield {"type": "token", "content": "hi"}

        with patch("layerkg.web.router.chat.run_query_stream", return_value=fake_stream()):
            resp = client.post("/api/chat/stream", json={"message": "test", "thread_id": "t1"})
            assert resp.status_code == 200
```

## Task 9：验证

```bash
# 全量测试
uv run pytest tests/ -v

# ruff
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/

# 手动验证 FastAPI 启动
uv run python -c "from layerkg.web.app import create_app; app = create_app(); print('OK')"
```
