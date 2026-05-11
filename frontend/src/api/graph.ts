import type { GraphData, GraphStats, NodeDetail } from './types'

const API_BASE = '/api'

export async function fetchGraph(params?: {
  center?: string
  depth?: number
  limit?: number
  type?: string
}): Promise<GraphData> {
  const sp = new URLSearchParams()
  if (params?.center) sp.set('center', params.center)
  if (params?.depth) sp.set('depth', String(params.depth))
  if (params?.limit) sp.set('limit', String(params.limit))
  if (params?.type) sp.set('type', params.type)
  const qs = sp.toString()
  const url = `${API_BASE}/graph${qs ? '?' + qs : ''}`
  const res = await fetch(url)
  if (!res.ok) throw new Error(`Failed to fetch graph: ${res.status}`)
  return res.json()
}

export async function fetchGraphStats(): Promise<GraphStats> {
  const res = await fetch(`${API_BASE}/graph/stats`)
  if (!res.ok) throw new Error(`Failed to fetch stats: ${res.status}`)
  return res.json()
}

export async function fetchNodeDetail(nodeId: string): Promise<NodeDetail> {
  const res = await fetch(`${API_BASE}/graph/node/${encodeURIComponent(nodeId)}`)
  if (!res.ok) throw new Error(`Failed to fetch node: ${res.status}`)
  return res.json()
}

export async function deleteNode(nodeId: string): Promise<{ status: string; id: string }> {
  const res = await fetch(`${API_BASE}/graph/node/${encodeURIComponent(nodeId)}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`Failed to delete node: ${res.status}`)
  return res.json()
}
