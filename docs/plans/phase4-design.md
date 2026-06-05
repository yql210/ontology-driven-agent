# Phase 4 方案设计：Web UI + 图谱可视化

## 一、目标

为 LayerKG Agent 提供 Web 界面，实现：
1. **对话界面** — 用户在浏览器中与 Agent 交互（替代 CLI）
2. **图谱可视化** — Neo4j 知识图谱的交互式可视化
3. **Langfuse 看板** — 嵌入 Langfuse traces 查看面板

## 二、技术选型

### 方案对比

| 方案 | 优点 | 缺点 | 评分 |
|------|------|------|------|
| **A: FastAPI + 原生前端** | 轻量、Python 全栈、部署简单 | 需要手写 HTML/JS | ⭐⭐⭐⭐⭐ |
| B: FastAPI + React SPA | 组件化、现代化 | 需要 Node.js 构建、过度工程 | ⭐⭐⭐ |
| C: Streamlit | 最快实现 | 定制性差、不适合图谱可视化 | ⭐⭐ |
| D: Gradio | 简单 | 图谱可视化受限 | ⭐⭐ |

**选择方案 A**：FastAPI 后端 + 原生 HTML/CSS/JS 前端（单文件 SPA）。

理由：
- 项目是 Python 全栈，不引入 Node.js 依赖
- 图谱可视化用 Cytoscape.js（CDN 引入，无需构建）
- 对话界面用 SSE（Server-Sent Events）实现流式输出
- 部署在远程服务器 `<YOUR_SERVER_IP>`，一个 `uvicorn` 进程搞定

### 核心技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| 后端框架 | FastAPI + uvicorn | 异步、WebSocket 支持 |
| 对话接口 | SSE (Server-Sent Events) | 流式输出 Agent 回复 |
| 图谱可视化 | Cytoscape.js | 专业图可视化库，支持力导向布局 |
| 前端 | 原生 HTML + Tailwind CSS (CDN) | 零构建，单文件 |
| Langfuse | iframe 嵌入 | 已部署的 Langfuse 实例 |

## 三、架构设计

```
浏览器
  ├── / (首页 — 对话界面)
  ├── /graph (图谱可视化)
  ├── /traces (Langfuse 看板)
  └── /api
       ├── POST /api/chat         ← 发送消息
       ├── GET  /api/chat/stream  ← SSE 流式回复
       ├── GET  /api/graph        ← 获取图谱数据（节点+边）
       ├── GET  /api/graph/{id}   ← 获取节点详情
       └── GET  /api/stats        ← 图谱统计信息

FastAPI (uvicorn)
  ├── router/chat.py     ← 对话路由（调用 agent.graph.run_query）
  ├── router/graph.py    ← 图谱路由（调用 neo4j_store）
  ├── router/pages.py    ← 页面路由（返回 HTML）
  └── static/            ← 静态资源（JS/CSS）
```

### 3.1 后端 API 设计

#### 对话 API

```python
# POST /api/chat
# Body: {"message": "xxx", "thread_id": "uuid-optional"}
# Response: {"answer": "...", "thread_id": "uuid", "tools_called": [...], "duration_ms": 1234}

# GET /api/chat/stream?message=xxx&thread_id=uuid
# Response: SSE 事件流
#   event: token, data: {"content": "..."}
#   event: tool_call, data: {"tool": "...", "args": {...}}
#   event: done, data: {"duration_ms": 1234}
```

**说明：** 流式输出需要改造现有 `run_query`。当前 `run_query` 是 `async def run_query(question, thread_id)` 返回字符串。需要增加 `run_query_stream` 变体，通过 `astream_events` 获取流式事件。

#### 图谱 API

```python
# GET /api/graph?center=ConceptAligner&depth=2
# Response: {
#   "nodes": [{"id": "...", "label": "ConceptAligner", "type": "CodeEntity", ...}],
#   "edges": [{"source": "...", "target": "...", "type": "CALLS"}]
# }
# 支持按中心节点 + 深度探索

# GET /api/graph/node/{node_id}
# Response: {"id": "...", "name": "...", "type": "...", "properties": {...}, "relations": [...]}

# GET /api/stats
# Response: {"nodes": 389, "edges": 749, "by_type": {"CodeEntity": 311, ...}}
```

### 3.2 前端页面设计

#### 页面 1：对话界面（`/`）

```
┌─────────────────────────────────────────┐
│  LayerKG Agent                    [图谱] │
├─────────────────────────────────────────┤
│                                         │
│  🤖 你好！我是 LayerKG 代码知识图谱助手  │
│  可以帮你理解代码架构、查询依赖关系...   │
│                                         │
│  👤 Neo4jGraphStore 继承自哪个类？       │
│                                         │
│  🤖 ConceptAligner 定义在...            │
│  ┌─────────────────────────────────┐    │
│  │ 🔧 semantic_search("...")      │    │
│  │ 🔧 graph_query("MATCH...")     │    │
│  └─────────────────────────────────┘    │
│  ConceptAligner 定义在 src/layerkg/     │
│  aligner.py 第 42-369 行...            │
│                                         │
├─────────────────────────────────────────┤
│  [输入问题...]                    [发送]  │
└─────────────────────────────────────────┘
```

特性：
- 工具调用折叠显示（可展开查看参数和结果）
- Markdown 渲染（代码高亮）
- 流式输出（逐字显示）
- 对话历史（前端维护 thread_id）

#### 页面 2：图谱可视化（`/graph`）

```
┌─────────────────────────────────────────┐
│  知识图谱    [搜索...] [类型▾] [重置]    │
├─────────────────────────────────────────┤
│  ┌──┐  CALLS   ┌──┐  CONTAINS ┌──────┐ │
│  │fn│ ───────→ │fn│ ────────→ │Module│ │
│  └──┘          └──┘           └──────┘ │
│       ↕ IMPORTS    ↕ EXTENDS           │
│  ┌──┐          ┌──┐                    │
│  │fn│          │cls│                   │
│  └──┘          └──┘                    │
├─────────────────────────────────────────┤
│  节点详情: [点击节点显示]                │
│  名称: ConceptAligner                   │
│  类型: class | 文件: aligner.py         │
│  关系: 8 条 (3 incoming, 5 outgoing)   │
└─────────────────────────────────────────┘
```

特性：
- Cytoscape.js 力导向布局（自动排列）
- 节点按类型着色（CodeEntity=蓝, ConceptEntity=绿, ModuleEntity=橙, DocEntity=紫）
- 点击节点展示详情面板
- 支持搜索、按类型筛选、拖拽、缩放
- 双击节点 → 以该节点为中心展开 2 层邻居

#### 页面 3：Langfuse 看板（`/traces`）

直接 iframe 嵌入已部署的 Langfuse 实例。

### 3.3 流式输出改造

当前 `agent/graph.py` 的 `run_query` 是一次性返回完整结果。需要新增流式变体：

```python
async def run_query_stream(question: str, thread_id: str | None = None):
    """流式运行 Agent，yield 事件"""
    agent = create_agent()
    config = {
        "configurable": {"thread_id": thread_id or str(uuid4())},
        "callbacks": [langfuse_handler] if langfuse_handler else [],
    }
    
    async for event in agent.astream_events(
        {"messages": [HumanMessage(content=question)]},
        config=config,
        version="v2",
    ):
        kind = event["event"]
        if kind == "on_chat_model_stream":
            # LLM token 流
            token = event["data"]["chunk"].content
            if token:
                yield {"type": "token", "content": token}
        elif kind == "on_tool_start":
            # 工具调用开始
            yield {"type": "tool_start", "tool": event["name"], "args": event["data"].get("input", {})}
        elif kind == "on_tool_end":
            # 工具调用结束
            yield {"type": "tool_end", "tool": event["name"], "output": event["data"].get("output", "")}
```

### 3.4 目录结构

```
src/layerkg/
├── web/                        # 新增 Web UI 模块
│   ├── __init__.py             # 导出 create_app
│   ├── app.py                  # FastAPI app 工厂
│   ├── router/
│   │   ├── __init__.py
│   │   ├── pages.py            # 页面路由（返回 HTML）
│   │   ├── chat.py             # 对话 API（SSE 流式）
│   │   └── graph.py            # 图谱 API
│   ├── static/
│   │   ├── index.html          # 对话页面（Tailwind + 原生 JS）
│   │   ├── graph.html          # 图谱可视化页面（Cytoscape.js）
│   │   ├── traces.html         # Langfuse 嵌入页面
│   │   ├── app.js              # 对话前端逻辑
│   │   ├── graph-viewer.js     # 图谱可视化逻辑
│   │   └── style.css           # 共享样式
│   └── templates/              # （备用，当前用静态 HTML）
├── agent/                      # 现有，不改
├── cli.py                      # 新增 web 命令
└── ...
```

**CLI 集成：**
```python
@cli.command()
@click.option('--host', default='0.0.0.0')
@click.option('--port', default=8000)
@click.option('--reload', is_flag=True)
def web(host, port, reload):
    """启动 LayerKG Web UI"""
    import uvicorn
    from layerkg.web.app import create_app
    app = create_app()
    uvicorn.run(app, host=host, port=port, reload=reload)
```

## 四、实施计划（4 天）

### Day 1：后端骨架 + 对话 API

**目标：** FastAPI 服务跑起来，对话 API 能用

| # | 任务 | 说明 |
|---|------|------|
| 1 | 安装依赖 | `uv add fastapi uvicorn sse-starlette` |
| 2 | 创建 web/ 目录结构 | app.py, router/, static/ |
| 3 | 实现 app.py | FastAPI app 工厂，挂载路由和静态文件 |
| 4 | 实现 chat router | POST /api/chat（同步）+ GET /api/chat/stream（SSE） |
| 5 | 改造 run_query_stream | 在 graph.py 新增流式变体 |
| 6 | CLI web 命令 | `layerkg web --port 8000` |
| 7 | 对话页面 HTML | index.html（基础聊天界面） |
| 8 | 测试 | API 测试 + 手动验证 |

**Day 1 验收：** 浏览器打开 `http://<YOUR_SERVER_IP>:8000`，输入问题，Agent 流式回复。

### Day 2：图谱可视化

**目标：** 交互式知识图谱可视化

| # | 任务 | 说明 |
|---|------|------|
| 1 | graph router | GET /api/graph（节点+边）、GET /api/graph/node/{id}、GET /api/stats |
| 2 | Cypher 查询封装 | 按中心节点+深度获取子图，全图获取（限 500 节点） |
| 3 | graph.html + graph-viewer.js | Cytoscape.js 力导向布局，节点着色 |
| 4 | 交互功能 | 点击详情、搜索、类型筛选、双击展开 |
| 5 | 性能优化 | 大图裁剪（>500 节点只显示核心）、懒加载 |
| 6 | 测试 | API 测试 + 手动验证 |

**Day 2 验收：** 浏览器打开 `/graph`，看到完整知识图谱，能交互探索。

### Day 3：UI 打磨 + Langfuse 嵌入

**目标：** 三个页面可用、美观

| # | 任务 | 说明 |
|---|------|------|
| 1 | 对话 UI 完善 | Markdown 渲染、工具调用折叠、加载动画 |
| 2 | 导航栏 + 页面切换 | 顶部导航栏，响应式布局 |
| 3 | Langfuse 嵌入 | iframe 嵌入 traces 页面 |
| 4 | 移动端适配 | Tailwind 响应式类 |
| 5 | 错误处理 | 网络断开、Agent 超时的友好提示 |
| 6 | 测试 | 全量测试 + ruff |

### Day 4：集成测试 + 部署 + 文档

**目标：** 可部署的完整 Web 应用

| # | 任务 | 说明 |
|---|------|------|
| 1 | 端到端测试 | API 测试 + UI 截图验证 |
| 2 | 部署到远程服务器 | `nohup uv run layerkg web --host 0.0.0.0 --port 8000` |
| 3 | README 更新 | Web UI 使用说明 + 截图 |
| 4 | 思源笔记记录 | Phase 4 进展更新 |
| 5 | 最终 commit | Phase 4 完成 |

## 五、依赖

```
新增依赖：
- fastapi >= 0.115         # Web 框架
- uvicorn >= 0.34          # ASGI 服务器
- sse-starlette >= 2.0     # SSE 支持（流式输出）

前端 CDN（无需安装）：
- Tailwind CSS (CDN)
- Cytoscape.js (CDN)
- marked.js (Markdown 渲染)
- highlight.js (代码高亮)
```

## 六、风险与缓解

| 风险 | 严重度 | 缓解策略 |
|------|--------|---------|
| 流式 astream_events API 不兼容 | 🟠 中 | 先实现同步 /api/chat，SSE 作为增强 |
| 图谱节点过多（>1000）浏览器卡顿 | 🟠 中 | 限制渲染数量 + 按需加载子图 |
| Cytoscape.js 学习曲线 | 🟡 低 | 官方文档完善，DAG/力导向布局够用 |
| 远程服务器端口 8000 未开放 | 🟡 低 | 可换端口或用 Nginx 反代 |
| CORS 问题 | 🟡 低 | FastAPI 同源部署，无跨域问题 |

## 七、不改什么（边界）

1. **不改 Agent 核心逻辑** — 只新增 `run_query_stream`，不改现有 `run_query`
2. **不改工具定义** — 8 个工具保持不变
3. **不改 Neo4j Schema** — 图谱只读查询
4. **不做用户认证** — 内部演示工具，不需要登录
5. **不做 SSR** — 纯静态 HTML + API，简单直接
