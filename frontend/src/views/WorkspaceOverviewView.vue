<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  fetchWorkspaceServiceGraphDirectory,
  WorkspaceGraphApiError,
  type WorkspaceGraphDirectory,
  type WorkspaceGraphNode,
} from '../api/workspace'

const route = useRoute()
const loading = ref(true)
const error = ref<WorkspaceGraphApiError | Error | null>(null)
const directory = ref<WorkspaceGraphDirectory | null>(null)

const workspaceId = computed(() => String(route.params.workspace_id ?? '').trim())
const principal = computed(() => String(route.query.principal ?? '').trim())
const requestParams = computed(() => ({
  generation_id: route.query.generation_id ? String(route.query.generation_id) : undefined,
  repo_id: route.query.repo_id ? String(route.query.repo_id) : undefined,
  page_size: route.query.page_size ? Number(route.query.page_size) : undefined,
  depth: route.query.depth ? Number(route.query.depth) : undefined,
  node_limit: route.query.node_limit ? Number(route.query.node_limit) : undefined,
  cursor: route.query.cursor ? String(route.query.cursor) : undefined,
}))

const nodes = computed(() => directory.value?.nodes ?? [])
const isEmpty = computed(() => Boolean(directory.value && nodes.value.length === 0 && directory.value.edges.length === 0))

function nodeRepoId(node: WorkspaceGraphNode): string | null {
  const value = node.repo_id ?? node.repoId
  return typeof value === 'string' && value ? value : null
}

function nodeRevision(node: WorkspaceGraphNode): string | null {
  for (const key of ['source_revision', 'sourceRevision', 'revision'] as const) {
    const value = node[key]
    if (typeof value === 'string' && value) return value
  }
  return null
}

const repoRevisions = computed(() => {
  const revisions = new Map<string, Set<string>>()
  for (const node of nodes.value) {
    const repoId = nodeRepoId(node)
    if (!repoId) continue
    if (!revisions.has(repoId)) revisions.set(repoId, new Set())
    const revision = nodeRevision(node)
    if (revision) revisions.get(repoId)?.add(revision)
  }
  return [...revisions.entries()].map(([repoId, values]) => ({ repoId, revisions: [...values] }))
})

const counters = computed(() => ({
  serviceOperations: nodes.value.filter((node) => node.node_type === 'ServiceOperation').length,
  unresolved: nodes.value.filter((node) => node.node_type === 'MethodUnresolved').length,
  evidence: nodes.value.filter((node) => node.node_type === 'MethodEvidence').length,
  edges: directory.value?.edges.length ?? 0,
}))

async function load() {
  if (!workspaceId.value || !principal.value) {
    loading.value = false
    directory.value = null
    error.value = new Error('Workspace and principal are required')
    return
  }

  loading.value = true
  error.value = null
  directory.value = null
  try {
    directory.value = await fetchWorkspaceServiceGraphDirectory(workspaceId.value, principal.value, requestParams.value)
  } catch (cause) {
    error.value = cause instanceof Error ? cause : new Error(String(cause))
  } finally {
    loading.value = false
  }
}

function errorTitle(status?: number) {
  if (status === 403) return 'Access denied'
  if (status === 409) return 'Generation conflict'
  if (status === 422) return 'Invalid request'
  return 'Unable to load workspace'
}

watch([workspaceId, principal, requestParams], load, { immediate: true })
</script>

<template>
  <main class="workspace-overview">
    <header class="overview-header">
      <div>
        <p class="eyebrow">Workspace service graph</p>
        <h1>{{ workspaceId || 'Workspace overview' }}</h1>
      </div>
      <nav class="overview-nav" aria-label="Workspace navigation">
        <router-link to="/graph">Graph</router-link>
        <router-link to="/repos">Repositories</router-link>
      </nav>
      <span v-if="directory" class="visibility" :class="directory.visibility">{{ directory.visibility }}</span>
    </header>

    <section v-if="loading" class="state-panel">
      <h2>Loading workspace graph</h2>
      <p>Fetching the visible directory...</p>
    </section>
    <section v-else-if="error" class="state-panel error-state">
      <h2>{{ errorTitle(error instanceof WorkspaceGraphApiError ? error.status : undefined) }}</h2>
      <p v-if="error instanceof WorkspaceGraphApiError && error.status === 403">This principal cannot access the workspace.</p>
      <p v-else-if="error instanceof WorkspaceGraphApiError && error.status === 409">The requested generation is no longer available.</p>
      <p v-else-if="error instanceof WorkspaceGraphApiError && error.status === 422">Check the workspace and query parameters.</p>
      <p v-else>{{ error.message }}</p>
    </section>
    <section v-else-if="isEmpty" class="state-panel">
      <h2>No visible graph data</h2>
      <p>The authorized directory contains no nodes or edges.</p>
    </section>
    <template v-else-if="directory">
      <section class="metadata">
        <div><span>Workspace</span><strong>{{ directory.workspace_id }}</strong></div>
        <div><span>Generation</span><strong>{{ directory.generation_id }}</strong></div>
        <div><span>Visibility</span><strong>{{ directory.visibility }}</strong></div>
      </section>
      <section v-if="directory.visibility === 'filtered'" class="notice">
        This view is filtered to the repositories visible to the current principal.
      </section>
      <section class="counter-grid">
        <div><span>ServiceOperation</span><strong>{{ counters.serviceOperations }}</strong></div>
        <div><span>MethodUnresolved</span><strong>{{ counters.unresolved }}</strong></div>
        <div><span>MethodEvidence</span><strong>{{ counters.evidence }}</strong></div>
        <div><span>Edges</span><strong>{{ counters.edges }}</strong></div>
      </section>
      <section class="repositories">
        <h2>Visible repositories</h2>
        <p v-if="repoRevisions.length === 0" class="muted">No repository provenance fields were returned.</p>
        <ul v-else>
          <li v-for="repo in repoRevisions" :key="repo.repoId">
            <strong>{{ repo.repoId }}</strong>
            <span v-if="repo.revisions.length">{{ repo.revisions.join(', ') }}</span>
          </li>
        </ul>
      </section>
    </template>
  </main>
</template>

<style scoped>
.workspace-overview { max-width: 1120px; margin: 0 auto; padding: 32px 24px 56px; }
.overview-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 20px; margin-bottom: 28px; }
.overview-nav { display: flex; gap: 14px; margin-left: auto; }
.overview-nav a { color: var(--text-secondary); text-decoration: none; }
.overview-nav a:hover, .overview-nav a.router-link-active { color: var(--text-primary); }
.eyebrow { color: var(--text-muted); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }
h1 { margin-top: 6px; font-size: 30px; color: var(--text-primary); }
.visibility { padding: 6px 10px; border: 1px solid var(--border-default); border-radius: var(--radius-sm); text-transform: uppercase; font-size: 12px; }
.visibility.filtered { color: #fbbf24; }
.visibility.full { color: #34d399; }
.state-panel, .metadata, .counter-grid > div, .repositories { border: 1px solid var(--border-default); background: var(--bg-card); border-radius: var(--radius-sm); }
.state-panel { padding: 32px; text-align: center; }
.state-panel h2 { font-size: 18px; margin-bottom: 8px; }
.state-panel p, .muted { color: var(--text-secondary); }
.error-state { border-color: rgba(248, 113, 113, .45); }
.metadata { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px; overflow: hidden; margin-bottom: 16px; }
.metadata div { padding: 18px; background: rgba(15, 23, 42, .35); }
.metadata span, .counter-grid span { display: block; color: var(--text-muted); font-size: 12px; margin-bottom: 8px; }
.metadata strong { overflow-wrap: anywhere; }
.notice { padding: 12px 16px; border-left: 3px solid #fbbf24; background: rgba(251, 191, 36, .08); color: var(--text-secondary); margin-bottom: 16px; }
.counter-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 24px; }
.counter-grid > div { padding: 18px; }
.counter-grid strong { font-size: 24px; }
.repositories { padding: 20px; }
.repositories h2 { font-size: 17px; margin-bottom: 14px; }
.repositories ul { list-style: none; }
.repositories li { display: flex; justify-content: space-between; gap: 16px; padding: 11px 0; border-top: 1px solid var(--border-dim); }
.repositories li span { color: var(--text-secondary); font-family: var(--font-mono); font-size: 12px; overflow-wrap: anywhere; text-align: right; }
@media (max-width: 700px) {
  .workspace-overview { padding: 24px 16px; }
  .overview-header { flex-wrap: wrap; }
  .overview-nav { order: 3; width: 100%; margin-left: 0; }
  .metadata, .counter-grid { grid-template-columns: repeat(2, 1fr); }
  .repositories li { align-items: flex-start; flex-direction: column; gap: 4px; }
  .repositories li span { text-align: left; }
}
</style>
