import type { TraceInfo, TraceListItem } from './types'

export async function listTraces(): Promise<TraceListItem[]> {
  const resp = await fetch('/api/trace/list')
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  return resp.json()
}

export async function getTrace(threadId: string): Promise<TraceInfo> {
  const resp = await fetch(`/api/trace/thread/${threadId}`)
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  return resp.json()
}

export async function getMermaid(): Promise<string> {
  const resp = await fetch('/api/trace/graph/mermaid')
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  const data = await resp.json()
  return data.mermaid
}

export async function deleteTrace(threadId: string): Promise<void> {
  const resp = await fetch(`/api/trace/thread/${threadId}`, { method: 'DELETE' })
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
}
