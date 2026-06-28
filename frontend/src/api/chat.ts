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
      if (!ev.data || !ev.data.trim()) return
      try {
        const data = JSON.parse(ev.data)
        const eventType = ev.event
        if (!['token', 'tool_start', 'tool_end', 'error', 'done'].includes(eventType)) {
          console.warn('Unknown SSE event type:', eventType)
          return
        }
        onEvent({ type: eventType as SSEEvent['type'], ...data } as SSEEvent)
      } catch {
        // SSE 数据不完整时（DeepSeek 断连等），跳过该片段而非中断整个流
        console.warn('SSE parse skipped (incomplete data)')
      }
    },
    onerror(err) {
      onError(err as Error)
    },
  })
}

export async function sendApproval(
  approvalId: string,
  approved: boolean,
): Promise<{ success: boolean; status: string; message: string }> {
  const resp = await fetch('/api/chat/approval', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ approval_id: approvalId, approved }),
  })
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  return resp.json()
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
