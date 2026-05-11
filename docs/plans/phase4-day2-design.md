# Phase 4 Day 2 方案设计：Vue 3 前端搭建 + 对话页面

## 一、背景

Day 1 已完成 FastAPI 后端骨架（commit `e9bf8d2`），提供：
- `POST /api/chat` — 同步对话 API
- `POST /api/chat/stream` — SSE 流式对话（token/tool_start/tool_end/error/done 事件）
- `GET /health` — 健康检查
- `layerkg web --reload` — CLI 启动命令

Day 2 目标：搭建 Vue 3 + Vite 前端项目，实现对话页面，能与后端 SSE 流式接口对接。

## 二、方案设计

### 2.1 技术选型

| 组件 | 技术 | 版本 | 说明 |
|------|------|------|------|
| 框架 | Vue 3 | 3.5+ | Composition API + `<script setup>` |
| 构建 | Vite | 6.x | 官方推荐，HMR 极快 |
| 语言 | TypeScript | 5.x | 类型安全 |
| SSE 客户端 | @microsoft/fetch-event-source | 2.x | **必须**：POST SSE，原生 EventSource 只支持 GET |
| 状态管理 | Pinia | 2.x | Vue 3 官方推荐 |
| 路由 | Vue Router | 4.x | SPA 页面切换 |
| Markdown | marked | 15.x | Agent 回复含代码块 |
| 代码高亮 | highlight.js | 11.x | 代码块语法高亮 |
| CSS | 原生 CSS + CSS Variables | — | 不引入 UI 框架，保持轻量 |

**不使用 UI 框架的理由**：Day 2 只需对话页面，原生 CSS 足够。避免引入 Element Plus / Ant Design Vue 等重框架。

### 2.2 目录结构

```
frontend/
├── index.html
├── package.json
├── vite.config.ts
├── tsconfig.json
├── tsconfig.app.json
├── tsconfig.node.json
├── env.d.ts
├── public/
│   └── favicon.svg
└── src/
    ├── App.vue                    # 根组件（布局 + 导航）
    ├── main.ts                    # Vue 入口
    ├── style.css                  # 全局样式 + CSS 变量
    ├── api/
    │   ├── chat.ts                # 封装 /api/chat 和 /api/chat/stream
    │   └── types.ts               # TS 类型定义（ChatRequest/Response/SSEEvent）
    ├── composables/
    │   └── useChat.ts             # 对话逻辑 composable（消息管理 + SSE）
    ├── stores/
    │   └── chat.ts                # Pinia store（消息列表、thread_id、loading 状态）
    ├── views/
    │   └── ChatView.vue           # 对话页面
    ├── components/
    │   ├── ChatInput.vue          # 输入框 + 发送按钮
    │   ├── MessageList.vue        # 消息列表容器
    │   ├── MessageBubble.vue      # 单条消息气泡（用户/AI）
    │   ├── ToolCallBlock.vue      # 工具调用折叠展示
    │   └── MarkdownRenderer.vue   # Markdown 渲染（marked + highlight.js）
    └── router/
        └── index.ts               # Vue Router 配置
```

### 2.3 关键设计决策

#### D0: 后端错误事件字段统一（M1 修复）

后端 `graph.py` L158 用 `{"type": "error", "message": str(e)}`，而 `chat.py` L59/64 用 `{"error": "..."}`。
**修复**：统一为 `{"type": "error", "message": "..."}` 格式，前端只需处理 `message` 字段。
此修复由 Claude Code 在 Day 2 实施时一并完成（改 chat.py 的 error 字段）。

#### D1: POST SSE 用 @microsoft/fetch-event-source

浏览器原生 `EventSource` 只支持 GET 请求，但后端 SSE 接口 `POST /api/chat/stream` 需要在 body 中传 JSON。`@microsoft/fetch-event-source` 支持 POST + SSE，是唯一选择。

替代方案（已否决）：
- `eventsource-polyfill` — 也只支持 GET
- 改后端为 GET — 违背 RESTful 设计，且 URL 编码消息有长度限制
- WebSocket — 过度工程，SSE 足够

#### D2: Vite proxy 转发 /api

开发时 Vite dev server 跑在 `localhost:5173`，后端在 `localhost:8000`。通过 Vite proxy 转发避免 CORS 问题（虽然后端已配 CORS，但 proxy 更干净）。

生产构建时 `npm run build` → 将 `frontend/dist/` 挂载到 FastAPI 的 static 目录，同源部署。

#### D3: Pinia 管理对话状态

消息列表、thread_id、loading 状态放入 Pinia store。好处：
- 组件间共享状态（ChatView、ChatInput、MessageList 都需要访问）
- 支持后续扩展（多对话、对话历史持久化）

#### D4: 工具调用折叠展示

Agent 调用工具时，前端收到 `tool_start` 和 `tool_end` 事件。设计：
- 工具调用显示为可折叠块（默认折叠）
- 折叠时显示工具图标 + 工具名
- 展开时显示完整参数

#### D5: Markdown 渲染 + 代码高亮

Agent 回复包含代码块、行内代码、列表等 Markdown 格式。使用 `marked` + `highlight.js` 渲染。

### 2.4 SSE 事件处理流程

```
用户输入 → ChatInput emit('send', message)
          → useChat.sendMessage(message)
            → 检查 isLoading（P1 S4: 防重复发送）
          → api/chat.ts fetchEventSource(POST /api/chat/stream)
          → 收到 SSE 事件:
              token → 追加到当前 AI 消息的 content（逐字显示）
              tool_start → 添加 ToolCall{id, tool, args, status:'running'}（M2）
              tool_end → 匹配 ToolCall.id，更新 status:'completed'
              error → 用 message 字段显示错误提示（M1 统一字段），添加 Message{role:'error'}（S1）
              done → 保存 thread_id 到 store，标记 loading=false，停止 isStreaming（M3）
```

### 2.5 消息数据模型

```typescript
// SSE 事件类型（M4 完整定义）
export type SSEEventType = 'token' | 'tool_start' | 'tool_end' | 'error' | 'done'

export interface SSETokenEvent { type: 'token'; content: string }
export interface SSEToolStartEvent { type: 'tool_start'; tool: string; args: Record<string, unknown> }
export interface SSEToolEndEvent { type: 'tool_end'; tool: string }
export interface SSEErrorEvent { type: 'error'; message: string }  // M1: 统一用 message
export interface SSEDoneEvent { type: 'done'; thread_id: string }
export type SSEEvent = SSETokenEvent | SSEToolStartEvent | SSEToolEndEvent | SSEErrorEvent | SSEDoneEvent

// 消息模型
interface Message {
  id: string
  role: 'user' | 'assistant' | 'error'  // S1: 增加 error role
  content: string
  toolCalls?: ToolCall[]
  isStreaming?: boolean
  timestamp: number
}

// 工具调用模型（M2: 增加 id）
interface ToolCall {
  id: string              // 唯一标识，格式: `${tool}-${timestamp}-${random}`
  tool: string
  args?: Record<string, unknown>
  status: 'running' | 'completed' | 'failed'  // S2: 增加 failed 状态
  error?: string
}
```

### 2.6 Vite 配置

```typescript
// vite.config.ts
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
```

### 2.7 .gitignore 更新

在项目根 `.gitignore` 追加：
```
# Frontend
frontend/node_modules/
frontend/dist/
```

### 2.8 Day 2 不做什么

1. **不做图谱可视化** — Day 3 任务
2. **不做 Langfuse 嵌入** — Day 4 任务
3. **不做用户认证** — 内部演示
4. **不做生产构建集成** — Day 4/5 任务（将 dist 挂到 FastAPI）
5. **不做响应式/移动端适配** — Day 4 任务

## 三、验收标准

1. `npm run dev` 启动前端，访问 `http://localhost:5173`
2. 输入问题，Agent **流式逐字回复**（SSE token 事件）
3. 工具调用显示为折叠块
4. 对话维持 thread_id，支持多轮对话
5. 后端 `layerkg web --reload` 同时运行
