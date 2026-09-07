<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  fetchWorkspaceOperationDirectory,
  fetchWorkspaceServiceGraphConsumers,
  fetchWorkspaceServiceGraphProviders,
  WorkspaceGraphApiError,
  type WorkspaceGraphDirectory,
  type WorkspaceGraphNode,
} from '../api/workspace'

const route = useRoute()
const workspaceId = computed(() => String(route.params.workspace_id ?? '').trim())
const principal = computed(() => String(route.query.principal ?? '').trim())
const generationId = computed(() => String(route.query.generation_id ?? '').trim() || undefined)
const repoFilter = ref(String(route.query.repo_id ?? '').trim())
const protocolFilter = ref('')
const pageSize = ref(Number(route.query.page_size ?? 50) || 50)
const cursor = ref<string | undefined>(String(route.query.cursor ?? '').trim() || undefined)
const directory = ref<WorkspaceGraphDirectory | null>(null)
const loading = ref(true)
const error = ref<Error | null>(null)
const selected = ref<WorkspaceGraphNode | null>(null)
const drilldown = ref<{ providers: WorkspaceGraphDirectory | null; consumers: WorkspaceGraphDirectory | null }>({ providers: null, consumers: null })
const drilldownLoading = ref(false)

const nodes = computed(() => directory.value?.nodes ?? [])
const repoOptions = computed(() => [...new Set(nodes.value.map((node) => node.repo_id ?? node.repoId).filter((v): v is string => typeof v === 'string' && v.length > 0))])
const protocolOptions = computed(() => [...new Set(nodes.value.map((node) => node.protocol).filter((v): v is string => typeof v === 'string' && v.length > 0))])
const filteredNodes = computed(() => nodes.value.filter((node) => {
  const repo = node.repo_id ?? node.repoId
  return (!repoFilter.value || repo === repoFilter.value) && (!protocolFilter.value || node.protocol === protocolFilter.value)
}))
const isEmpty = computed(() => Boolean(directory.value && nodes.value.length === 0))
const isFilteredEmpty = computed(() => Boolean(directory.value && nodes.value.length > 0 && filteredNodes.value.length === 0))

function params() {
  return { generation_id: generationId.value, repo_id: repoFilter.value || undefined, page_size: pageSize.value, cursor: cursor.value }
}
async function load() {
  if (!workspaceId.value || !principal.value) { error.value = new Error('Workspace and principal are required'); loading.value = false; return }
  loading.value = true; error.value = null; directory.value = null
  try { directory.value = await fetchWorkspaceOperationDirectory(workspaceId.value, principal.value, params()) }
  catch (cause) { error.value = cause instanceof Error ? cause : new Error(String(cause)) }
  finally { loading.value = false }
}
function protocol(node: WorkspaceGraphNode) { return typeof node.protocol === 'string' ? node.protocol : '' }
function label(node: WorkspaceGraphNode) { return String(node.displayName ?? node.operationName ?? node.canonicalSignature ?? node.id) }
function endpointKey(node: WorkspaceGraphNode) { const value = node.canonical_key ?? node.canonicalKey ?? node.endpoint_key ?? node.endpointKey; return typeof value === 'string' && value ? value : node.id }
async function showDrilldown(node: WorkspaceGraphNode) {
  const key = endpointKey(node); if (!key) return
  selected.value = node; drilldownLoading.value = true; drilldown.value = { providers: null, consumers: null }
  try {
    const base = params()
    const [providers, consumers] = await Promise.all([
      fetchWorkspaceServiceGraphProviders(workspaceId.value, principal.value, key, base),
      fetchWorkspaceServiceGraphConsumers(workspaceId.value, principal.value, key, base),
    ])
    drilldown.value = { providers, consumers }
  } catch (cause) { error.value = cause instanceof Error ? cause : new Error(String(cause)) }
  finally { drilldownLoading.value = false }
}
function errorTitle() { const status = error.value instanceof WorkspaceGraphApiError ? error.value.status : 0; return status === 403 ? 'Access denied' : status === 409 ? 'Generation conflict' : status === 422 ? 'Invalid request' : 'Unable to load service directory' }
watch([workspaceId, principal, generationId], load, { immediate: true })
</script>

<template>
  <main class="service-directory">
    <header><div><p class="eyebrow">Workspace services</p><h1>Operation directory</h1><p class="subtle">{{ workspaceId }} · generation {{ directory?.generation_id ?? generationId ?? 'current' }}</p></div><router-link :to="`/workspaces/${workspaceId}`">Overview</router-link></header>
    <section class="filters" aria-label="Directory filters"><label>Repository<select v-model="repoFilter"><option value="">All returned repositories</option><option v-for="repo in repoOptions" :key="repo" :value="repo">{{ repo }}</option></select></label><label>Protocol<select v-model="protocolFilter"><option value="">All returned protocols</option><option v-for="protocolValue in protocolOptions" :key="protocolValue" :value="protocolValue">{{ protocolValue }}</option></select></label><label>Page size<input v-model.number="pageSize" type="number" min="1" max="100" /></label><button type="button" @click="cursor = undefined; load()">Refresh</button></section>
    <section v-if="loading" class="state"><h2>Loading service directory</h2></section>
    <section v-else-if="error" class="state error"><h2>{{ errorTitle() }}</h2><p>{{ error.message }}</p></section>
    <section v-else-if="isEmpty" class="state"><h2>No operations returned</h2></section>
    <section v-else-if="isFilteredEmpty" class="state"><h2>No operations match these filters</h2></section>
    <template v-else-if="directory"><p class="visibility">Visibility: {{ directory.visibility }}</p><div class="operation-list"><article v-for="node in filteredNodes" :key="node.id"><div><h2>{{ label(node) }}</h2><p>{{ node.id }}</p><p v-if="protocol(node)">{{ protocol(node) }}</p><p v-if="node.repo_id ?? node.repoId">repo: {{ node.repo_id ?? node.repoId }}</p></div><button type="button" :disabled="!endpointKey(node)" @click="showDrilldown(node)">Providers / consumers</button></article></div><nav v-if="directory.next_cursor" class="pagination"><button type="button" @click="cursor = directory?.next_cursor ?? undefined; load()">Next page</button></nav></template>
    <aside v-if="selected" class="drilldown"><h2>{{ label(selected) }}</h2><p v-if="drilldownLoading">Loading providers and consumers...</p><template v-else><div><h3>Providers</h3><ul><li v-for="node in drilldown.providers?.nodes ?? []" :key="node.id">{{ label(node) }}</li><li v-if="!(drilldown.providers?.nodes.length)">No providers returned</li></ul></div><div><h3>Consumers</h3><ul><li v-for="node in drilldown.consumers?.nodes ?? []" :key="node.id">{{ label(node) }}</li><li v-if="!(drilldown.consumers?.nodes.length)">No consumers returned</li></ul></div></template></aside>
  </main>
</template>

<style scoped>
.service-directory { max-width: 1120px; margin: 0 auto; padding: 32px 24px 56px; }
header { display:flex; justify-content:space-between; align-items:flex-start; gap:20px; margin-bottom:24px; } h1 { margin:6px 0; font-size:30px; } .eyebrow,.subtle,.operation-list p,.visibility { color:var(--text-secondary); font-size:13px; } header a { color:var(--text-secondary); }
.filters { display:flex; flex-wrap:wrap; gap:14px; align-items:end; padding:16px 0; border-block:1px solid var(--border-default); margin-bottom:20px; } label { display:flex; flex-direction:column; gap:6px; color:var(--text-secondary); font-size:12px; } select,input,button { background:var(--bg-secondary); color:var(--text-primary); border:1px solid var(--border-default); border-radius:var(--radius-sm); padding:9px 11px; } button { cursor:pointer; } button:disabled { opacity:.5; cursor:not-allowed; }
.operation-list { display:grid; gap:10px; } article,.drilldown,.state { border:1px solid var(--border-default); background:var(--bg-card); border-radius:var(--radius-sm); padding:18px; } article { display:flex; justify-content:space-between; gap:16px; align-items:center; } article h2 { font-size:16px; margin-bottom:6px; } .state { text-align:center; padding:36px; } .error { border-color:rgba(248,113,113,.45); } .pagination { margin-top:18px; } .drilldown { margin-top:24px; display:grid; gap:18px; } .drilldown h2 { font-size:18px; } .drilldown h3 { font-size:14px; margin-bottom:8px; } li { padding:5px 0; color:var(--text-secondary); }
@media (max-width:700px) { .service-directory { padding:24px 16px; } article { align-items:flex-start; flex-direction:column; } header { flex-wrap:wrap; } }
</style>
