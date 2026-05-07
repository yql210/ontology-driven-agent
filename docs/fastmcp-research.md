# FastMCP (Python MCP Server SDK) 调研报告

## 基本信息

| 项目 | 详情 |
|------|------|
| **PyPI 包名** | `fastmcp` |
| **最新版本** | 3.2.4 |
| **仓库** | https://github.com/PrefectHQ/fastmcp |
| **文档** | https://gofastmcp.com |
| **Python 要求** | >=3.10 |
| **核心依赖** | mcp>=1.24.0, pydantic>=2.11.7, httpx, uvicorn, starlette, cyclopts |

## 1. 安装方式

```bash
# 推荐 uv
uv pip install fastmcp
uv add fastmcp

# 或 pip
pip install fastmcp

# 可选依赖
pip install "fastmcp[tasks]"     # 后台任务
pip install "fastmcp[apps]"      # App UI
pip install "fastmcp[openai]"    # OpenAI 集成
pip install "fastmcp[anthropic]" # Anthropic 集成
pip install "fastmcp[gemini]"    # Gemini 集成

# 验证安装
fastmcp version
```

## 2. 服务器创建方式

### 基础创建

```python
from fastmcp import FastMCP

# 最简方式
mcp = FastMCP("Demo 🚀")

# 带说明
mcp = FastMCP(
    "DataAnalysis",
    instructions="Provides tools for analyzing datasets. Start with get_summary().",
)
```

### 构造函数参数（v3.x）

| 参数 | 类型 | 说明 |
|------|------|------|
| `name` | `str` | 服务器名称 |
| `instructions` | `str \| None` | 给 LLM 的使用说明 |
| `version` | `str \| None` | 版本号 |
| `tools` | `list \| None` | 预注册工具列表 |
| `auth` | `OAuthProvider \| None` | 认证提供者 |
| `middleware` | `list \| None` | 中间件 |
| `providers` | `list \| None` | 动态组件提供者 |
| `transforms` | `list \| None` | 组件变换 |
| `lifespan` | `Lifespan \| None` | 生命周期管理 |
| `on_duplicate` | `"warn"|"error"|"replace"|"ignore"` | 重复注册行为 |
| `strict_input_validation` | `bool` | 严格输入验证 |
| `mask_error_details` | `bool \| None` | 隐藏内部错误详情 |
| `tasks` | `bool` | 启用后台任务 |

## 3. 组件注册装饰器

### 3.1 工具（Tools）— `@mcp.tool`

```python
@mcp.tool
def add(a: int, b: int) -> int:
    """Add two numbers"""
    return a + b

# 带参数
@mcp.tool(
    name="find_products",
    description="Search the product catalog.",
    tags={"catalog", "search"},
    timeout=30.0,
    annotations={"readOnlyHint": True},
)
def search_products(query: str, category: str | None = None) -> list[dict]:
    ...
```

**支持的参数类型**：`int`, `float`, `str`, `bool`, `bytes`, `datetime`, `list[str]`, `dict[str, int]`, `Optional[T]`, `Union`, `Literal`, `Enum`, `Path`, `UUID`, Pydantic 模型等。

**返回值类型**：`str`, `int`, `float`, `dict`, `list`, `ToolResult`, `bytes`, 或任何可序列化类型。

**支持同步和异步函数**：
```python
@mcp.tool
async def async_tool(query: str) -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"https://api.example.com/search?q={query}")
        return resp.text
```

### 3.2 资源（Resources）— `@mcp.resource`

```python
@mcp.resource("resource://greeting")
def get_greeting() -> str:
    """Provides a greeting message."""
    return "Hello from FastMCP!"

# 带 MIME 类型
@mcp.resource("data://config", mime_type="application/json")
def get_config() -> str:
    import json
    return json.dumps({"theme": "dark", "version": "1.0"})
```

**返回值**：`str`（文本）, `bytes`（二进制）, `ResourceResult`（完整控制）

### 3.3 资源模板（Resource Templates）— `@mcp.resource` 带参数 URI

```python
@mcp.resource("users://{user_id}/profile")
def get_user_profile(user_id: int) -> str:
    return json.dumps({"id": user_id, "name": "User"})
```

### 3.4 提示词（Prompts）— `@mcp.prompt`

```python
@mcp.prompt
def analyze_data(data_points: list[float]) -> str:
    formatted = ", ".join(str(p) for p in data_points)
    return f"Please analyze these data points: {formatted}"

# 多消息
from fastmcp.prompts import Message

@mcp.prompt
def generate_code(language: str, task: str) -> list[Message]:
    return [
        Message(f"Write a {language} function: {task}"),
        Message("I'll help you write that.", role="assistant"),
    ]
```

### 3.5 自定义路由 — `@mcp.custom_route`

```python
from starlette.requests import Request
from starlette.responses import PlainTextResponse

@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> PlainTextResponse:
    return PlainTextResponse("OK")
```

### 3.6 编程式注册

```python
from fastmcp.tools import tool

class Calculator:
    @tool()
    def multiply(self, x: int) -> int:
        return x * self.multiplier

calc = Calculator(3)
mcp.add_tool(calc.multiply)
```

## 4. 服务器启动方式

### 4.1 Python 直接运行

```python
if __name__ == "__main__":
    # STDIO（默认，适合本地/桌面客户端）
    mcp.run()

    # HTTP（Streamable HTTP，推荐用于远程部署）
    mcp.run(transport="http", host="127.0.0.1", port=8000)

    # SSE（已废弃，向后兼容）
    mcp.run(transport="sse", host="127.0.0.1", port=8000)
```

HTTP 服务器默认端点：`http://localhost:8000/mcp`

### 4.2 CLI 运行

```bash
# 自动发现 mcp/server/app 变量
fastmcp run server.py

# 显式指定
fastmcp run server.py:mcp

# 指定传输和端口
fastmcp run server.py:mcp --transport http --port 8000

# 开发热重载
fastmcp run server.py --reload

# 依赖管理
fastmcp run server.py --with pandas --with numpy
fastmcp run server.py --with-requirements requirements.txt
fastmcp run server.py --python 3.11

# 传递参数
fastmcp run config_server.py -- --config config.json
```

### 4.3 异步运行

```python
import asyncio

async def main():
    # 异步 HTTP
    await mcp.run_http_async(host="127.0.0.1", port=8000)

asyncio.run(main())
```

## 5. 客户端连接

```python
import asyncio
from fastmcp import Client

async def main():
    # HTTP 连接
    async with Client("http://localhost:8000/mcp") as client:
        tools = await client.list_tools()
        result = await client.call_tool("add", {"a": 1, "b": 2})
        print(result)

asyncio.run(main())
```

## 6. 标签过滤（v2.8+）

```python
@mcp.tool(tags={"public", "utility"})
def public_tool() -> str:
    return "public"

# 仅暴露带 "public" 标签的组件
mcp.enable(tags={"public"}, only=True)

# 隐藏带 "internal" 标签的组件
mcp.disable(tags={"internal", "deprecated"})
```

## 7. 中间件 / 生命周期

```python
from fastmcp.server.middleware import Middleware

# 中间件
mcp = FastMCP("MyServer", middleware=[MyMiddleware()])

# 生命周期（异步上下文管理器）
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(server):
    # 启动时初始化
    db = await connect_db()
    yield {"db": db}
    # 关闭时清理
    await db.close()

mcp = FastMCP("MyServer", lifespan=lifespan)
```

## 8. 与旧版本的主要变化（v2 → v3）

- `host`/`port`/`sse_path` 等参数不再在构造函数中设置，改为 `run()` 或环境变量
- `on_duplicate_tools` → 统一为 `on_duplicate`
- `include_tags`/`exclude_tags` → `mcp.enable()` / `mcp.disable()`
- `tool_serializer` → 使用 `ToolResult` 返回
- `tool_transformations` → `server.add_transform()`
- `enabled` 参数在 v3 中废弃，改用服务器级 enable/disable
