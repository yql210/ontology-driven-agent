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
