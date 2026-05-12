// SSE 事件类型
export type SSEEventType = 'token' | 'tool_start' | 'tool_end' | 'error' | 'done'

export interface SSETokenEvent { type: 'token'; content: string }
export interface SSEToolStartEvent { type: 'tool_start'; tool: string; args: Record<string, unknown> }
export interface SSEToolEndEvent { type: 'tool_end'; tool: string; result?: string }
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
  result?: string
}

// ========= Graph Types =========

export interface GraphNode {
  id: string
  name: string
  neo4jLabel: string
  entity_type?: string
}

export interface GraphEdge {
  source: string
  target: string
  type: string
}

export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export interface GraphStats {
  node_count: number
  edge_count: number
  by_type: Record<string, number>
}

export interface NodeDetail {
  id: string
  name: string
  neo4jLabel: string
  properties: Record<string, unknown>
  relations: {
    incoming: Array<{ source_id: string; source_name: string; type: string }>
    outgoing: Array<{ target_id: string; target_name: string; type: string }>
  }
}

// ========= Trace Types =========

export interface TraceStep {
  step_id: number
  type: 'thinking' | 'tool_call' | 'tool_result' | 'final'
  content: string
  tool_name?: string
  tool_args?: string
  tool_result?: string
  duration_ms?: number
}

export interface TraceInfo {
  thread_id: string
  query: string
  status: 'running' | 'completed' | 'failed'
  steps: TraceStep[]
  total_duration_ms?: number
}

export interface TraceListItem {
  thread_id: string
  query: string
  status: 'running' | 'completed' | 'failed'
  step_count: number
  total_duration_ms?: number
  created_at: number
}
