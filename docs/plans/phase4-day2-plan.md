# Phase 4 Day 2 实施计划：Vue 3 前端搭建 + 对话页面

## 前置条件
- 后端 Day 1 已完成（commit `e9bf8d2`），FastAPI 运行在 localhost:8000
- Node.js 20 LTS + npm 9.2.0 已安装
- 方案设计文档：`docs/plans/phase4-day2-design.md`

## 执行批次

### Batch 1: 项目脚手架 + 基础配置（Task 1-4）

#### Task 1: 创建 Vue 3 + Vite 项目
```bash
cd /opt/data/workspace/ontology-driven-agent
npm create vite@latest frontend -- --template vue-ts
cd frontend
npm install
```

验证：`cd frontend && npm run dev` 能启动。

#### Task 2: 安装前端依赖
```bash
cd /opt/data/workspace/ontology-driven-agent/frontend
npm install @microsoft/fetch-event-source pinia vue-router@4 marked highlight.js
```

#### Task 3: 配置 vite.config.ts
替换 `frontend/vite.config.ts`：
```typescript
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

#### Task 4: 更新 .gitignore + 清理默认文件
在项目根 `.gitignore` 追加：
```
# Frontend
frontend/node_modules/
frontend/dist/
```

删除 Vite 默认生成的 `frontend/src/components/HelloWorld.vue` 和 `frontend/src/assets/vue.svg`。

### Batch 2: 类型定义 + API 封装 + Store（Task 5-7）

#### Task 5: 创建类型定义 `frontend/src/api/types.ts`
```typescript
// SSE 事件类型
export type SSEEventType = 'token' | 'tool_start' | 'tool_end' | 'error' | 'done'

export interface SSETokenEvent { type: 'token'; content: string }
export interface SSEToolStartEvent { type: 'tool_start'; tool: string; args: Record<string, unknown> }
export interface SSEToolEndEvent { type: 'tool_end'; tool: string }
export interface SSEErrorEvent { type: 'error'; message: string }
export interface SSEDoneEvent { type: 'done'; thread_id: string }
export type SSEEvent = SSETokenEvent | SSEToolStartEvent | SSEToolEndEvent | SSEErrorEvent | SSEDoneEvent

// 对话请求/响应
export interface ChatRequest {
  message: string
  thread_id?: string | null
}

export interface ChatResponse {
  answer: string
  thread_id: string
  duration_ms: number
}

// 消息模型
export interface Message {
  id: string
  role: 'user' | 'assistant' | 'error'
  content: string
  toolCalls?: ToolCall[]
  isStreaming?: boolean
  timestamp: number
}

export interface ToolCall {
  id: string
  tool: string
  args?: Record<string, unknown>
  status: 'running' | 'completed' | 'failed'
  error?: string
}
```

#### Task 6: 创建 API 封装 `frontend/src/api/chat.ts`
```typescript
import { fetchEventSource } from '@microsoft/fetch-event-source'
import type { ChatRequest, SSEEvent } from './types'

export async function sendChatStream(
  req: ChatRequest,
  onEvent: (event: SSEEvent) => void,
  onError: (err: Error) => void,
): Promise<void> {
  await fetchEventSource('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
    onmessage(ev) {
      const data = JSON.parse(ev.data)
      const eventType = ev.event
      // 验证事件类型（P0-2 防御性检查）
      if (!['token', 'tool_start', 'tool_end', 'error', 'done'].includes(eventType)) {
        console.warn('Unknown SSE event type:', eventType)
        return
      }
      onEvent({ type: eventType as SSEEvent['type'], ...data } as SSEEvent)
    },
    onerror(err) {
      onError(err as Error)
    },
  })
}

export async function sendChatSync(req: ChatRequest): Promise<{ answer: string; thread_id: string; duration_ms: number }> {
  const resp = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  return resp.json()
}
```

#### Task 7: 创建 Pinia Store `frontend/src/stores/chat.ts`
```typescript
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { sendChatStream } from '../api/chat'
import type { Message, ToolCall, SSEEvent } from '../api/types'

export const useChatStore = defineStore('chat', () => {
  const messages = ref<Message[]>([])
  const threadId = ref<string | null>(null)
  const isLoading = ref(false)

  function addUserMessage(content: string) {
    messages.value.push({
      id: `user-${Date.now()}`,
      role: 'user',
      content,
      timestamp: Date.now(),
    })
  }

  function addAssistantMessage(): Message {
    const msg: Message = {
      id: `assistant-${Date.now()}`,
      role: 'assistant',
      content: '',
      toolCalls: [],
      isStreaming: true,
      timestamp: Date.now(),
    }
    messages.value.push(msg)
    return msg
  }

  async function sendMessage(content: string) {
    if (!content.trim() || isLoading.value) return
    isLoading.value = true
    addUserMessage(content)

    const assistantMsg = addAssistantMessage()

    try {
      await sendChatStream(
        { message: content, thread_id: threadId.value },
        (event: SSEEvent) => {
          const lastMsg = messages.value[messages.value.length - 1]
          if (!lastMsg) return

          switch (event.type) {
            case 'token':
              lastMsg.content += event.content
              break
            case 'tool_start': {
              const toolCall: ToolCall = {
                id: `tc-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
                tool: event.tool,
                args: event.args,
                status: 'running',
              }
              if (!lastMsg.toolCalls) lastMsg.toolCalls = []
              lastMsg.toolCalls.push(toolCall)
              break
            }
            case 'tool_end': {
              // P0-3: 匹配最后一个同名 running 工具（而非第一个），支持连续调用
              const calls = lastMsg.toolCalls
              if (calls) {
                for (let i = calls.length - 1; i >= 0; i--) {
                  if (calls[i].tool === event.tool && calls[i].status === 'running') {
                    calls[i].status = 'completed'
                    break
                  }
                }
              }
              break
            }
            case 'error':
              lastMsg.content += `\n\n⚠️ 错误: ${event.message}`
              break
            case 'done':
              if (event.thread_id) threadId.value = event.thread_id
              lastMsg.isStreaming = false
              isLoading.value = false
              break
          }
        },
        (err: Error) => {
          const lastMsg = messages.value[messages.value.length - 1]
          if (lastMsg) {
            lastMsg.content += `\n\n⚠️ 连接错误: ${err.message}`
            lastMsg.isStreaming = false
          }
          isLoading.value = false
        },
      )
    } catch (err) {
      const lastMsg = messages.value[messages.value.length - 1]
      if (lastMsg) {
        lastMsg.content += `\n\n⚠️ 发送失败: ${(err as Error).message}`
        lastMsg.isStreaming = false
      }
      isLoading.value = false
    }
  }

  return { messages, threadId, isLoading, sendMessage }
})
```

### Batch 3: Vue 组件 + 页面（Task 8-11）

#### Task 8: 创建路由 `frontend/src/router/index.ts`
```typescript
import { createRouter, createWebHistory } from 'vue-router'
import ChatView from '../views/ChatView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'chat', component: ChatView },
  ],
})

export default router
```

#### Task 9: 创建组件

**`frontend/src/components/MarkdownRenderer.vue`**
```vue
<script setup lang="ts">
import { computed } from 'vue'
import { marked } from 'marked'
import hljs from 'highlight.js'
import 'highlight.js/styles/github-dark.css'

const props = defineProps<{ content: string }>()

marked.setOptions({
  highlight(code: string, lang: string) {
    if (lang && hljs.getLanguage(lang)) {
      return hljs.highlight(code, { language: lang }).value
    }
    return hljs.highlightAuto(code).value
  },
})

const rendered = computed(() => marked.parse(props.content || ''))
</script>

<template>
  <div class="markdown-body" v-html="rendered"></div>
</template>

<style scoped>
.markdown-body :deep(pre) {
  background: #1e1e2e;
  border-radius: 6px;
  padding: 12px;
  overflow-x: auto;
}
.markdown-body :deep(code) {
  font-family: 'Fira Code', monospace;
  font-size: 0.9em;
}
.markdown-body :deep(p) {
  margin: 0.5em 0;
}
</style>
```

**`frontend/src/components/ToolCallBlock.vue`**
```vue
<script setup lang="ts">
import { ref } from 'vue'
import type { ToolCall } from '../api/types'

const props = defineProps<{ toolCall: ToolCall }>()
const expanded = ref(false)
</script>

<template>
  <div class="tool-call" :class="toolCall.status">
    <div class="tool-header" @click="expanded = !expanded">
      <span class="tool-icon">🔧</span>
      <span class="tool-name">{{ toolCall.tool }}</span>
      <span class="tool-status">{{ toolCall.status === 'running' ? '⏳' : '✅' }}</span>
      <span class="tool-toggle">{{ expanded ? '▾' : '▸' }}</span>
    </div>
    <div v-if="expanded && toolCall.args" class="tool-args">
      <pre>{{ JSON.stringify(toolCall.args, null, 2) }}</pre>
    </div>
  </div>
</template>

<style scoped>
.tool-call {
  border-left: 3px solid #4a9eff;
  margin: 4px 0;
  border-radius: 4px;
  background: #f8f9fa;
}
.tool-call.running { border-left-color: #f0ad4e; }
.tool-call.completed { border-left-color: #5cb85c; }
.tool-header {
  padding: 6px 10px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.85em;
}
.tool-args pre {
  margin: 0;
  padding: 8px 10px;
  background: #1e1e2e;
  color: #d4d4d4;
  font-size: 0.8em;
  border-radius: 0 0 4px 4px;
}
</style>
```

**`frontend/src/components/MessageBubble.vue`**
```vue
<script setup lang="ts">
import type { Message } from '../api/types'
import MarkdownRenderer from './MarkdownRenderer.vue'
import ToolCallBlock from './ToolCallBlock.vue'

defineProps<{ message: Message }>()
</script>

<template>
  <div class="message" :class="message.role">
    <div class="avatar">{{ message.role === 'user' ? '👤' : '🤖' }}</div>
    <div class="bubble">
      <MarkdownRenderer :content="message.content" />
      <div v-if="message.toolCalls?.length" class="tool-calls">
        <ToolCallBlock v-for="tc in message.toolCalls" :key="tc.id" :tool-call="tc" />
      </div>
      <span v-if="message.isStreaming" class="cursor">▊</span>
    </div>
  </div>
</template>

<style scoped>
.message {
  display: flex;
  gap: 10px;
  margin: 12px 0;
  max-width: 800px;
}
.message.user { flex-direction: row-reverse; }
.avatar {
  font-size: 1.5em;
  flex-shrink: 0;
}
.bubble {
  padding: 10px 14px;
  border-radius: 12px;
  background: #f0f0f0;
  line-height: 1.6;
  min-width: 60px;
}
.message.user .bubble { background: #007bff; color: white; }
.message.error .bubble { background: #fff3cd; color: #856404; }
.cursor {
  animation: blink 0.7s infinite;
}
@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}
</style>
```

**`frontend/src/components/ChatInput.vue`**
```vue
<script setup lang="ts">
import { ref } from 'vue'

const emit = defineEmits<{ send: [message: string] }>()
defineProps<{ disabled: boolean }>()
const input = ref('')

function handleSubmit() {
  const msg = input.value.trim()
  if (!msg) return
  emit('send', msg)
  input.value = ''
}
</script>

<template>
  <div class="chat-input">
    <input
      v-model="input"
      placeholder="输入问题，如：ConceptAligner 在哪个文件？"
      :disabled="disabled"
      @keydown.enter="handleSubmit"
    />
    <button :disabled="disabled || !input.trim()" @click="handleSubmit">发送</button>
  </div>
</template>

<style scoped>
.chat-input {
  display: flex;
  gap: 8px;
  padding: 12px 16px;
  background: white;
  border-top: 1px solid #e0e0e0;
}
input {
  flex: 1;
  padding: 10px 14px;
  border: 1px solid #ddd;
  border-radius: 8px;
  font-size: 14px;
  outline: none;
}
input:focus { border-color: #007bff; }
button {
  padding: 10px 20px;
  background: #007bff;
  color: white;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
}
button:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
```

#### Task 10: 创建 ChatView 页面 `frontend/src/views/ChatView.vue`
```vue
<script setup lang="ts">
import { useChatStore } from '../stores/chat'
import { ref } from 'vue'
import MessageBubble from '../components/MessageBubble.vue'
import ChatInput from '../components/ChatInput.vue'

const chatStore = useChatStore()
const messagesContainer = ref<HTMLElement>()

function handleSend(message: string) {
  chatStore.sendMessage(message)
  // 滚动到底部
  setTimeout(() => {
    messagesContainer.value?.scrollTo({ top: messagesContainer.value.scrollHeight, behavior: 'smooth' })
  }, 50)
}
</script>

<template>
  <div class="chat-view">
    <header class="chat-header">
      <h1>🔍 LayerKG Agent</h1>
      <p>代码知识图谱助手 — 问任何关于代码架构的问题</p>
    </header>
    <div ref="messagesContainer" class="messages">
      <div v-if="chatStore.messages.length === 0" class="empty-state">
        <p>👋 你好！我是 LayerKG 代码知识图谱助手</p>
        <p>可以帮你理解代码架构、查询依赖关系、搜索函数定义...</p>
      </div>
      <MessageBubble
        v-for="msg in chatStore.messages"
        :key="msg.id"
        :message="msg"
      />
    </div>
    <ChatInput :disabled="chatStore.isLoading" @send="handleSend" />
  </div>
</template>

<style scoped>
.chat-view {
  display: flex;
  flex-direction: column;
  height: 100vh;
  max-width: 900px;
  margin: 0 auto;
}
.chat-header {
  padding: 16px 20px;
  border-bottom: 1px solid #e0e0e0;
  background: white;
}
.chat-header h1 { margin: 0; font-size: 1.3em; }
.chat-header p { margin: 4px 0 0; color: #666; font-size: 0.9em; }
.messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px 20px;
  background: #fafafa;
}
.empty-state {
  text-align: center;
  color: #999;
  margin-top: 80px;
}
</style>
```

#### Task 11: 更新入口文件

**`frontend/src/main.ts`**
```typescript
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import router from './router'
import App from './App.vue'
import './style.css'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
```

**`frontend/src/App.vue`**
```vue
<script setup lang="ts">
</script>

<template>
  <router-view />
</template>

<style>
</style>
```

**`frontend/src/style.css`**
```css
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: #f5f5f5;
  color: #333;
}

::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #ccc; border-radius: 3px; }
```

### Batch 4: 后端修复 + 验证（Task 12-13）

#### Task 12: 修复后端 SSE 错误事件字段（M1）
修改 `src/layerkg/web/router/chat.py` L59 和 L64：
- `{"error": "Agent timeout"}` → `{"type": "error", "message": "Agent timeout"}`
- `{"error": str(e)}` → `{"type": "error", "message": str(e)}`

#### Task 13: 端到端验证
```bash
# 1. 后端测试（确认后端修改不破坏测试）
cd /opt/data/workspace/ontology-driven-agent
uv run pytest tests/ -v

# 2. 前端构建验证
cd frontend
npm run build

# 3. 手动验证（两个终端同时运行）
# 终端1: uv run layerkg web --reload
# 终端2: cd frontend && npm run dev
# 浏览器打开 http://localhost:5173，输入问题测试流式对话
```
