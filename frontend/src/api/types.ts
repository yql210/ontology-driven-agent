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
