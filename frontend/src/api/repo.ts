import type {
  BuildRequest,
  BuildStatus,
  BuildTriggerResponse,
  Repository,
} from './types'

const API_BASE = '/api'

export const repoApi = {
  async listRepos(): Promise<Repository[]> {
    const res = await fetch(`${API_BASE}/repos`)
    if (!res.ok) throw new Error(`Failed to fetch repos: ${res.status}`)
    const data = (await res.json()) as { repos?: Repository[] }
    return data.repos ?? []
  },

  async registerRepo(input: {
    name: string
    url: string
    branch?: string
  }): Promise<Repository> {
    const res = await fetch(`${API_BASE}/repos`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    })
    if (!res.ok) throw new Error(`Failed to register repo: ${res.status}`)
    return res.json()
  },

  async triggerBuild(req: BuildRequest): Promise<BuildTriggerResponse> {
    const res = await fetch(`${API_BASE}/build`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
    })
    if (!res.ok) throw new Error(`Failed to trigger build: ${res.status}`)
    return res.json()
  },

  async getBuildStatus(taskId: string): Promise<BuildStatus> {
    const res = await fetch(
      `${API_BASE}/build/status/${encodeURIComponent(taskId)}`,
    )
    if (!res.ok) throw new Error(`Failed to fetch build status: ${res.status}`)
    return res.json()
  },
}
