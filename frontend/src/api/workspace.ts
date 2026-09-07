export interface WorkspaceGraphNode {
  id: string
  node_type: string
  repo_id?: string
  source_revision?: string
  sourceRevision?: string
  revision?: string
  [key: string]: unknown
}
export interface WorkspaceGraphEdge {
  id?: string
  source_id: string
  target_id?: string
  relation_type?: string
  [key: string]: unknown
}
export interface WorkspaceGraphDirectory {
  workspace_id: string
  generation_id: string
  visibility: 'full' | 'filtered'
  nodes: WorkspaceGraphNode[]
  edges: WorkspaceGraphEdge[]
  next_cursor: string | null
}
export interface WorkspaceGraphDirectoryParams {
  generation_id?: string
  repo_id?: string
  page_size?: number
  depth?: number
  node_limit?: number
  cursor?: string
}
export class WorkspaceGraphApiError extends Error {
  readonly status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = 'WorkspaceGraphApiError'
    this.status = status
  }
}
export async function fetchWorkspaceServiceGraphDirectory(
  workspaceId: string,
  principal: string,
  params: WorkspaceGraphDirectoryParams = {},
): Promise<WorkspaceGraphDirectory> {
  return fetchWorkspaceServiceGraph(workspaceId, principal, 'directory', params)
}

export async function fetchWorkspaceOperationDirectory(
  workspaceId: string,
  principal: string,
  params: WorkspaceGraphDirectoryParams = {},
): Promise<WorkspaceGraphDirectory> {
  return fetchWorkspaceServiceGraph(workspaceId, principal, 'operations', params)
}

export async function fetchWorkspaceServiceGraphProviders(
  workspaceId: string,
  principal: string,
  endpointKey: string,
  params: WorkspaceGraphDirectoryParams = {},
): Promise<WorkspaceGraphDirectory> {
  return fetchWorkspaceServiceGraph(workspaceId, principal, 'providers', { ...params, endpoint_key: endpointKey })
}

export async function fetchWorkspaceServiceGraphConsumers(
  workspaceId: string,
  principal: string,
  endpointKey: string,
  params: WorkspaceGraphDirectoryParams = {},
): Promise<WorkspaceGraphDirectory> {
  return fetchWorkspaceServiceGraph(workspaceId, principal, 'consumers', { ...params, endpoint_key: endpointKey })
}

export async function fetchWorkspaceServiceGraphDependencies(
  workspaceId: string,
  principal: string,
  nodeId: string,
  params: WorkspaceGraphDirectoryParams = {},
): Promise<WorkspaceGraphDirectory> {
  return fetchWorkspaceServiceGraph(workspaceId, principal, 'dependencies', { ...params, node_id: nodeId })
}

export async function fetchWorkspaceServiceGraphEvidence(
  workspaceId: string,
  principal: string,
  nodeId: string,
  params: WorkspaceGraphDirectoryParams = {},
): Promise<WorkspaceGraphDirectory> {
  return fetchWorkspaceServiceGraph(workspaceId, principal, 'evidence', { ...params, node_id: nodeId })
}

export async function fetchWorkspaceServiceGraphImpact(
  workspaceId: string,
  principal: string,
  nodeId: string,
  params: WorkspaceGraphDirectoryParams = {},
): Promise<WorkspaceGraphDirectory> {
  return fetchWorkspaceServiceGraph(workspaceId, principal, 'impact', { ...params, node_id: nodeId })
}

async function fetchWorkspaceServiceGraph(
  workspaceId: string,
  principal: string,
  operation: 'directory' | 'operations' | 'providers' | 'consumers' | 'dependencies' | 'evidence' | 'impact',
  params: WorkspaceGraphDirectoryParams & { endpoint_key?: string; node_id?: string } = {},
): Promise<WorkspaceGraphDirectory> {
  // Supported workspace route family includes service-graph/directory, service-graph/dependencies, and service-graph/evidence.
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') search.set(key, String(value))
  }
  const query = search.toString()
  const url = `/api/workspaces/${encodeURIComponent(workspaceId)}/service-graph/${operation}${query ? `?${query}` : ''}`
  const response = await fetch(url, {
    headers: { 'X-Workspace-Principal': principal },
  })
  if (!response.ok) {
    let detail = `Workspace graph request failed (${response.status})`
    try {
      const body = (await response.json()) as { detail?: string }
      if (body.detail) detail = body.detail
    } catch {
      // Keep the status-based message for non-JSON responses.
    }
    throw new WorkspaceGraphApiError(response.status, detail)
  }
  return (await response.json()) as WorkspaceGraphDirectory
}
