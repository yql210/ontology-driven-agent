<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  fetchWorkspaceOperationDirectory,
  fetchWorkspaceServiceGraphDependencies,
  fetchWorkspaceServiceGraphDirectory,
  fetchWorkspaceServiceGraphEvidence,
  WorkspaceGraphApiError,
  type WorkspaceGraphDirectory,
  type WorkspaceGraphNode,
} from '../api/workspace'

const route = useRoute()
const workspaceId = computed(() => String(route.params.workspace_id ?? '').trim())
const principal = computed(() => String(route.query.principal ?? '').trim())
const generationId = ref(String(route.query.generation_id ?? '').trim())
const repoFilter = ref(String(route.query.repo_id ?? '').trim())
const pageSize = ref(Number(route.query.page_size ?? 50) || 50)
const serviceDirectory = ref<WorkspaceGraphDirectory | null>(null)
const operationDirectory = ref<WorkspaceGraphDirectory | null>(null)
const serviceCursor = ref<string | undefined>()
const operationCursor = ref<string | undefined>()
const loading = ref(true)
const loadingMore = ref(false)
const error = ref<Error | null>(null)
const selected = ref<WorkspaceGraphNode | null>(null)
const dependencyResult = ref<WorkspaceGraphDirectory | null>(null)
const dependencyLoading = ref(false)
const evidenceResult = ref<WorkspaceGraphDirectory | null>(null)
const evidenceLoading = ref(false)
const evidenceOpen = ref(false)

const nodes = computed(() => {
  const byId = new Map<string, WorkspaceGraphNode>()
  for (const node of [...(serviceDirectory.value?.nodes ?? []), ...(operationDirectory.value?.nodes ?? [])]) {
    if (!byId.has(node.id)) byId.set(node.id, node)
  }
  return [...byId.values()]
})
const nodeIds = computed(() => new Set(nodes.value.map((node) => node.id)))
const edges = computed(() => {
  const byId = new Map<string, WorkspaceGraphDirectory['edges'][number]>()
  for (const edge of [...(serviceDirectory.value?.edges ?? []), ...(operationDirectory.value?.edges ?? [])]) {
    if (!nodeIds.value.has(edge.source_id) || !edge.target_id || !nodeIds.value.has(edge.target_id)) continue
    const id = edge.id ?? `${edge.source_id}:${edge.target_id}:${edge.relation_type ?? ''}`
    if (!byId.has(id)) byId.set(id, edge)
  }
  return [...byId.values()]
})
const repoOptions = computed(() => [...new Set(nodes.value.map((node) => node.repo_id ?? node.repoId).filter((value): value is string => typeof value === 'string' && value.length > 0))])
const visibleNodes = computed(() => nodes.value.filter((node) => !repoFilter.value || (node.repo_id ?? node.repoId) === repoFilter.value))
const visibleIds = computed(() => new Set(visibleNodes.value.map((node) => node.id)))
const visibleEdges = computed(() => edges.value.filter((edge) => visibleIds.value.has(edge.source_id) && !!edge.target_id && visibleIds.value.has(edge.target_id)))
const empty = computed(() => Boolean(!loading.value && nodes.value.length === 0))
const filteredEmpty = computed(() => Boolean(!loading.value && nodes.value.length > 0 && visibleNodes.value.length === 0))
const nextAvailable = computed(() => Boolean(serviceCursor.value || operationCursor.value))

function property(node: WorkspaceGraphNode, ...keys: string[]): string {
  for (const key of keys) if (typeof node[key] === 'string' && node[key]) return node[key] as string
  return ''
}
function label(node: WorkspaceGraphNode): string {
  return property(node, 'canonicalSignature', 'canonical_signature', 'displayName', 'display_name', 'operationName', 'operation_name', 'serviceName', 'service_name', 'filePath', 'file_path', 'id')
}
function sourceRevision(node: WorkspaceGraphNode): string { return property(node, 'sourceRevision', 'source_revision', 'revision') }
function sourcePath(node: WorkspaceGraphNode): string { return property(node, 'filePath', 'file_path') }
function nodeKind(node: WorkspaceGraphNode): string { return String(node.node_type ?? node.nodeType ?? 'Node') }
function endpoint(node: WorkspaceGraphNode): string { return String(node.endpoint ?? node.endpoint_key ?? node.canonical_key ?? node.canonicalKey ?? '') }
function params(cursor?: string) {
  return { generation_id: generationId.value || undefined, repo_id: repoFilter.value || undefined, page_size: pageSize.value, cursor }
}
function merge(target: typeof serviceDirectory, page: WorkspaceGraphDirectory) {
  if (!target.value) target.value = page
  else target.value = { ...page, nodes: [...target.value.nodes, ...page.nodes], edges: [...target.value.edges, ...page.edges] }
}
async function load(reset = true) {
  if (!workspaceId.value || !principal.value) { error.value = new Error('Workspace and principal are required'); loading.value = false; return }
  if (reset) { loading.value = true; error.value = null; serviceDirectory.value = null; operationDirectory.value = null; serviceCursor.value = undefined; operationCursor.value = undefined }
  else { loadingMore.value = true }
  try {
    const [services, operations] = await Promise.all([
      reset || serviceCursor.value
        ? fetchWorkspaceServiceGraphDirectory(workspaceId.value, principal.value, params(reset ? undefined : serviceCursor.value))
        : Promise.resolve(null),
      reset || operationCursor.value
        ? fetchWorkspaceOperationDirectory(workspaceId.value, principal.value, params(reset ? undefined : operationCursor.value))
        : Promise.resolve(null),
    ])
    if (reset) {
      serviceDirectory.value = services
      operationDirectory.value = operations
    } else {
      if (services) merge(serviceDirectory, services)
      if (operations) merge(operationDirectory, operations)
    }
    if (services) serviceCursor.value = services.next_cursor ?? undefined
    if (operations) operationCursor.value = operations.next_cursor ?? undefined
  } catch (cause) { error.value = cause instanceof Error ? cause : new Error(String(cause)) }
  finally { loading.value = false; loadingMore.value = false }
}
async function showDependencies(node: WorkspaceGraphNode) {
  selected.value = node; dependencyResult.value = null; dependencyLoading.value = true
  try { dependencyResult.value = await fetchWorkspaceServiceGraphDependencies(workspaceId.value, principal.value, node.id, params()) }
  catch (cause) { error.value = cause instanceof Error ? cause : new Error(String(cause)) }
  finally { dependencyLoading.value = false }
}
async function showEvidence(node: WorkspaceGraphNode) {
  selected.value = node; evidenceOpen.value = true; evidenceResult.value = null; evidenceLoading.value = true
  try { evidenceResult.value = await fetchWorkspaceServiceGraphEvidence(workspaceId.value, principal.value, node.id, params()) }
  catch (cause) { error.value = cause instanceof Error ? cause : new Error(String(cause)) }
  finally { evidenceLoading.value = false }
}
function errorTitle() {
  const status = error.value instanceof WorkspaceGraphApiError ? error.value.status : 0
  return status === 403 ? 'Access denied' : status === 409 ? 'Generation conflict' : status === 422 ? 'Invalid request' : 'Unable to load topology'
}
watch([workspaceId, principal], () => load(), { immediate: true })
watch([generationId, repoFilter, pageSize], () => { if (workspaceId.value && principal.value) load() })
</script>

<template>
  <main class="topology">
    <header>
      <div>
        <p class="eyebrow">Workspace service graph</p>
        <h1>Dependency topology</h1>
        <p class="subtle">{{ workspaceId }} · generation {{ (serviceDirectory?.generation_id ?? generationId) || 'current' }}</p>
      </div>
      <router-link :to="`/workspaces/${workspaceId}`">Overview</router-link>
    </header>
    <section class="filters" aria-label="Topology filters">
      <label>
        Repository
        <select v-model="repoFilter">
          <option value="">All returned repositories</option>
          <option v-for="repo in repoOptions" :key="repo" :value="repo">{{ repo }}</option>
        </select>
      </label>
      <label>
        Generation
        <input v-model="generationId" placeholder="Current generation" />
      </label>
      <label>
        Page size
        <input v-model.number="pageSize" type="number" min="1" max="100" />
      </label>
      <button type="button" @click="load()">Refresh</button>
    </section>
    <section v-if="loading" class="state">
      <h2>Loading dependency topology</h2>
    </section>
    <section v-else-if="error" class="state error">
      <h2>{{ errorTitle() }}</h2>
      <p>{{ error.message }}</p>
    </section>
    <section v-else-if="empty" class="state">
      <h2>No visible topology data</h2>
    </section>
    <section v-else-if="filteredEmpty" class="state">
      <h2>No returned nodes match this repository</h2>
    </section>
    <template v-else>
      <p class="visibility">Visibility: {{ serviceDirectory?.visibility ?? operationDirectory?.visibility }} · {{ visibleNodes.length }} nodes · {{ visibleEdges.length }} edges</p>
      <div class="topology-layout">
        <section class="node-list">
          <article v-for="node in visibleNodes" :key="node.id" class="node" data-testid="topology-node">
            <button class="node-button" type="button" @click="showDependencies(node)">
              <strong>{{ label(node) }}</strong>
              <span>{{ nodeKind(node) }}</span>
              <small>{{ node.id }}</small>
              <small v-if="endpoint(node)">{{ endpoint(node) }}</small>
              <small v-if="sourceRevision(node)">{{ sourceRevision(node) }}</small>
            </button>
            <button class="evidence-button" type="button" @click="showEvidence(node)">Evidence</button>
          </article>
          <p v-if="visibleEdges.length === 0" class="muted">No edges connect the returned visible nodes.</p>
          <div v-else class="edge-list">
            <h2>Visible dependencies</h2>
            <p v-for="edge in visibleEdges" :key="edge.id ?? `${edge.source_id}-${edge.target_id}`">
              {{ edge.source_id }} → {{ edge.target_id }} <span>{{ edge.relation_type ?? 'dependency' }}</span>
            </p>
          </div>
          <button v-if="nextAvailable" type="button" :disabled="loadingMore" @click="load(false)">
            {{ loadingMore ? 'Loading...' : 'Next page' }}
          </button>
        </section>
        <aside v-if="selected" class="detail" data-testid="topology-detail">
          <h2>{{ label(selected) }}</h2>
          <p class="muted">{{ selected.id }}</p>
          <p v-if="dependencyLoading">Loading dependencies...</p>
          <template v-else-if="dependencyResult">
            <h3>Method / endpoint dependencies</h3>
            <ul>
              <li v-for="node in dependencyResult.nodes" :key="node.id">{{ label(node) }} <small>{{ node.id }}</small></li>
              <li v-if="dependencyResult.nodes.length === 0">No dependencies returned</li>
            </ul>
          </template>
        </aside>
      </div>
    </template>
    <aside v-if="evidenceOpen" class="drawer">
      <button type="button" @click="evidenceOpen = false">Close</button>
      <h2>Evidence</h2>
      <p v-if="evidenceLoading">Loading evidence...</p>
      <template v-else-if="evidenceResult">
        <ul>
          <li v-for="node in evidenceResult.nodes" :key="node.id">{{ label(node) }} <small>{{ node.id }}</small><small v-if="sourcePath(node)">{{ sourcePath(node) }} <template v-if="sourceRevision(node)">@ {{ sourceRevision(node) }}</template></small></li>
          <li v-if="evidenceResult.nodes.length === 0">No evidence returned</li>
        </ul>
      </template>
    </aside>
  </main>
</template>

<style scoped>
.topology { max-width: 1180px; margin: 0 auto; padding: 32px 24px 56px; } header { display:flex; justify-content:space-between; gap:20px; margin-bottom:24px; } h1 { margin:6px 0; font-size:30px; } .eyebrow,.subtle,.muted,.visibility { color:var(--text-secondary); font-size:13px; } header a { color:var(--text-secondary); }
.filters { display:flex; flex-wrap:wrap; gap:14px; align-items:end; border-block:1px solid var(--border-default); padding:16px 0; margin-bottom:20px; } label { display:flex; flex-direction:column; gap:6px; color:var(--text-secondary); font-size:12px; } select,input,button { background:var(--bg-secondary); color:var(--text-primary); border:1px solid var(--border-default); border-radius:var(--radius-sm); padding:9px 11px; } button { cursor:pointer; } button:disabled { opacity:.5; }
.state,.node,.detail,.drawer { border:1px solid var(--border-default); background:var(--bg-card); border-radius:var(--radius-sm); padding:18px; } .state { text-align:center; padding:36px; } .error { border-color:rgba(248,113,113,.45); } .topology-layout { display:grid; grid-template-columns:minmax(0,1fr) 320px; gap:18px; } .node-list { display:grid; gap:10px; } .node { display:flex; justify-content:space-between; gap:10px; align-items:center; } .node-button { border:0; background:transparent; text-align:left; padding:0; display:grid; gap:4px; flex:1; } .node-button strong { font-size:15px; } .node-button span,.node-button small,li small { color:var(--text-secondary); font-size:12px; overflow-wrap:anywhere; } .evidence-button { flex:none; } .detail { align-self:start; position:sticky; top:76px; } .detail h2,.edge-list h2,.drawer h2 { font-size:18px; margin-bottom:8px; } .detail ul,.drawer ul { list-style:none; margin-top:14px; } li { padding:8px 0; border-top:1px solid var(--border-dim); } .edge-list { margin-top:14px; border-top:1px solid var(--border-default); padding-top:14px; } .edge-list p { padding:7px 0; font-family:var(--font-mono); font-size:12px; overflow-wrap:anywhere; } .edge-list span { color:var(--text-secondary); } .drawer { position:fixed; z-index:20; inset:72px 20px 20px auto; width:min(380px,calc(100vw - 40px)); overflow:auto; box-shadow:0 12px 30px rgba(0,0,0,.3); } .drawer > button { float:right; }
@media (max-width:760px) { .topology { padding:24px 16px; } .topology-layout { grid-template-columns:1fr; } .detail { position:static; } header { flex-wrap:wrap; } .node { align-items:flex-start; flex-direction:column; } }
</style>
