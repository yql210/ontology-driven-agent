<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  fetchWorkspaceServiceGraphChanges,
  fetchWorkspaceServiceGraphImpact,
  fetchWorkspaceServiceGraphUnresolved,
  WorkspaceGraphApiError,
  type WorkspaceGraphDirectory,
  type WorkspaceGraphNode,
} from '../api/workspace'

const route = useRoute()
const workspaceId = computed(() => String(route.params.workspace_id ?? '').trim())
const principal = computed(() => String(route.query.principal ?? '').trim())
const generationId = computed(() => String(route.query.generation_id ?? '').trim() || undefined)
const fromGeneration = computed(() => String(route.query.from_generation ?? '').trim())
const pageSize = computed(() => {
  const value = Number(route.query.page_size ?? 50)
  return Number.isInteger(value) && value >= 1 && value <= 100 ? value : 50
})

const changes = ref<WorkspaceGraphDirectory | null>(null)
const unresolved = ref<WorkspaceGraphDirectory | null>(null)
const changesCursor = ref<string | undefined>()
const unresolvedCursor = ref<string | undefined>()
const loading = ref(true)
const loadingChangesMore = ref(false)
const loadingUnresolvedMore = ref(false)
const error = ref<Error | null>(null)
const selected = ref<WorkspaceGraphNode | null>(null)
const impact = ref<WorkspaceGraphDirectory | null>(null)
const impactLoading = ref(false)

const changedNodes = computed(() => changes.value?.nodes ?? [])
const changedNodeIds = computed(() => new Set(changedNodes.value.map((node) => node.id)))
const changedEdges = computed(() => (changes.value?.edges ?? []).filter((edge) => {
  const targetId = edge.target_id
  if (targetId === undefined) return false
  return changedNodeIds.value.has(edge.source_id) && changedNodeIds.value.has(targetId)
}))
const unresolvedNodes = computed(() => unresolved.value?.nodes ?? [])
const noDifference = computed(() => Boolean(changes.value && changedNodes.value.length === 0 && changedEdges.value.length === 0))
const noUnresolved = computed(() => Boolean(unresolved.value && unresolvedNodes.value.length === 0))

function params(cursor?: string) {
  return { generation_id: generationId.value, page_size: pageSize.value, cursor }
}
function append(page: WorkspaceGraphDirectory | null, next: WorkspaceGraphDirectory): WorkspaceGraphDirectory {
  return page ? { ...next, nodes: [...page.nodes, ...next.nodes], edges: [...page.edges, ...next.edges] } : next
}
function nodeLabel(node: WorkspaceGraphNode): string {
  return String(node.displayName ?? node.operationName ?? node.canonicalSignature ?? node.subject ?? node.id)
}
function nodeKind(node: WorkspaceGraphNode): string { return String(node.node_type ?? node.nodeType ?? 'Node') }
function nodeText(node: WorkspaceGraphNode, key: string): string | null {
  const value = node[key]
  return typeof value === 'string' && value ? value : null
}
function hasConflict(node: WorkspaceGraphNode): boolean { return node.conflict === true || node.is_conflict === true || node.isConflict === true }
function hasLowConfidence(node: WorkspaceGraphNode): boolean { return node.low_confidence === true || node.lowConfidence === true }
function errorTitle() {
  const status = error.value instanceof WorkspaceGraphApiError ? error.value.status : 0
  return status === 403 ? 'Access denied' : status === 409 ? 'Generation conflict' : status === 422 ? 'Invalid request' : 'Unable to load change quality'
}

async function load() {
  if (!workspaceId.value || !principal.value || !generationId.value || !fromGeneration.value) {
    changes.value = null; unresolved.value = null; error.value = new Error('Workspace, principal, generation, and from generation are required'); loading.value = false
    return
  }
  loading.value = true; error.value = null; selected.value = null; impact.value = null; changesCursor.value = undefined; unresolvedCursor.value = undefined
  try {
    const base = params()
    const [changePage, unresolvedPage] = await Promise.all([
      fetchWorkspaceServiceGraphChanges(workspaceId.value, principal.value, fromGeneration.value, base),
      fetchWorkspaceServiceGraphUnresolved(workspaceId.value, principal.value, base),
    ])
    changes.value = changePage; unresolved.value = unresolvedPage
    changesCursor.value = changePage.next_cursor ?? undefined; unresolvedCursor.value = unresolvedPage.next_cursor ?? undefined
  } catch (cause) { error.value = cause instanceof Error ? cause : new Error(String(cause)) }
  finally { loading.value = false }
}
async function loadMoreChanges() {
  if (!changesCursor.value) return
  loadingChangesMore.value = true
  try {
    const page = await fetchWorkspaceServiceGraphChanges(workspaceId.value, principal.value, fromGeneration.value, params(changesCursor.value))
    changes.value = append(changes.value, page); changesCursor.value = page.next_cursor ?? undefined
  } catch (cause) { error.value = cause instanceof Error ? cause : new Error(String(cause)) }
  finally { loadingChangesMore.value = false }
}
async function loadMoreUnresolved() {
  if (!unresolvedCursor.value) return
  loadingUnresolvedMore.value = true
  try {
    const page = await fetchWorkspaceServiceGraphUnresolved(workspaceId.value, principal.value, params(unresolvedCursor.value))
    unresolved.value = append(unresolved.value, page); unresolvedCursor.value = page.next_cursor ?? undefined
  } catch (cause) { error.value = cause instanceof Error ? cause : new Error(String(cause)) }
  finally { loadingUnresolvedMore.value = false }
}
async function showImpact(node: WorkspaceGraphNode) {
  if (!changedNodeIds.value.has(node.id)) return
  selected.value = node; impact.value = null; impactLoading.value = true
  try { impact.value = await fetchWorkspaceServiceGraphImpact(workspaceId.value, principal.value, node.id, params()) }
  catch (cause) { error.value = cause instanceof Error ? cause : new Error(String(cause)) }
  finally { impactLoading.value = false }
}

watch([workspaceId, principal, generationId, fromGeneration, pageSize], load, { immediate: true })
</script>

<template>
  <main class="change-quality">
    <header>
      <div>
        <p class="eyebrow">Workspace service graph</p>
        <h1>Change and quality</h1>
        <p class="subtle">{{ workspaceId }} · {{ fromGeneration || 'from generation required' }} to {{ changes?.generation_id ?? generationId ?? 'current' }}</p>
      </div>
      <router-link :to="`/workspaces/${workspaceId}`">Overview</router-link>
    </header>

    <section v-if="loading" class="state"><h2>Loading visible change records</h2></section>
    <section v-else-if="error" class="state error"><h2>{{ errorTitle() }}</h2><p>{{ error.message }}</p></section>
    <template v-else-if="changes && unresolved">
      <p class="visibility">Visibility: {{ changes.visibility }}</p>
      <section v-if="changes.visibility === 'filtered'" class="notice">This comparison is limited to repositories visible to the current principal.</section>
      <section class="generations" aria-label="Generation comparison"><div><span>From generation</span><strong>{{ fromGeneration }}</strong></div><div><span>Current generation</span><strong>{{ changes.generation_id }}</strong></div></section>

      <section class="panel">
        <h2>Changed nodes</h2>
        <p v-if="noDifference" class="muted">No visible differences were returned for these generations.</p>
        <div v-else class="record-list">
          <article v-for="node in changedNodes" :key="node.id"><div><strong>{{ nodeLabel(node) }}</strong><span>{{ nodeKind(node) }}</span><small>{{ node.id }}</small><small v-if="nodeText(node, 'repo_id')">{{ nodeText(node, 'repo_id') }}</small><small v-if="nodeText(node, 'reason_code')">{{ nodeText(node, 'reason_code') }}</small><small v-if="hasConflict(node)">Conflict</small><small v-if="hasLowConfidence(node)">Low confidence</small></div><button type="button" @click="showImpact(node)">Impact</button></article>
          <div v-if="changedEdges.length" class="edges"><h3>Changed edges</h3><p v-for="edge in changedEdges" :key="edge.id ?? `${edge.source_id}:${edge.target_id}:${edge.relation_type ?? ''}`">{{ edge.source_id }} → {{ edge.target_id }}<span v-if="edge.relation_type">{{ edge.relation_type }}</span></p></div>
        </div>
        <button v-if="changesCursor" type="button" :disabled="loadingChangesMore" @click="loadMoreChanges">{{ loadingChangesMore ? 'Loading...' : 'More changed records' }}</button>
      </section>

      <section class="panel">
        <h2>Unresolved records</h2>
        <p v-if="noUnresolved" class="muted">No visible unresolved records were returned.</p>
        <div v-else class="record-list"><article v-for="node in unresolvedNodes" :key="node.id"><div><strong>{{ nodeLabel(node) }}</strong><span>{{ nodeKind(node) }}</span><small>{{ node.id }}</small><small v-if="nodeText(node, 'reason_code')">{{ nodeText(node, 'reason_code') }}</small><small v-if="hasConflict(node)">Conflict</small><small v-if="hasLowConfidence(node)">Low confidence</small></div></article></div>
        <button v-if="unresolvedCursor" type="button" :disabled="loadingUnresolvedMore" @click="loadMoreUnresolved">{{ loadingUnresolvedMore ? 'Loading...' : 'More unresolved records' }}</button>
      </section>

      <aside v-if="selected" class="panel impact"><h2>Impact: {{ nodeLabel(selected) }}</h2><p v-if="impactLoading">Loading impact...</p><template v-else-if="impact"><p v-if="impact.nodes.length === 0 && impact.edges.length === 0" class="muted">No visible impact records were returned.</p><div v-else><h3>Impact nodes</h3><p v-for="node in impact.nodes" :key="node.id">{{ nodeLabel(node) }} <span>{{ nodeKind(node) }}</span></p><h3 v-if="impact.edges.length">Impact edges</h3><p v-for="edge in impact.edges" :key="edge.id ?? `${edge.source_id}:${edge.target_id}:${edge.relation_type ?? ''}`">{{ edge.source_id }} → {{ edge.target_id }}<span v-if="edge.relation_type">{{ edge.relation_type }}</span></p></div></template></aside>
    </template>
  </main>
</template>

<style scoped>
.change-quality { max-width: 1120px; margin: 0 auto; padding: 32px 24px 56px; }
header { display: flex; justify-content: space-between; align-items: flex-start; gap: 20px; margin-bottom: 24px; } h1 { margin: 6px 0; font-size: 30px; } header a { color: var(--text-secondary); } .eyebrow,.subtle,.visibility,.muted,small,article span,.edges span,.impact span { color: var(--text-secondary); font-size: 13px; }
.state,.panel,.generations { border: 1px solid var(--border-default); background: var(--bg-card); border-radius: var(--radius-sm); } .state { text-align: center; padding: 36px; } .error { border-color: rgba(248,113,113,.45); } .notice { padding: 12px 16px; border-left: 3px solid #fbbf24; background: rgba(251,191,36,.08); color: var(--text-secondary); margin: 16px 0; } .visibility { text-transform: uppercase; }
.generations { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); margin: 16px 0; overflow: hidden; } .generations div { padding: 16px; background: rgba(15,23,42,.35); } .generations span { display: block; color: var(--text-muted); font-size: 12px; margin-bottom: 7px; } .generations strong { overflow-wrap: anywhere; }
.panel { padding: 20px; margin-top: 16px; } .panel h2 { font-size: 18px; margin-bottom: 14px; } .record-list { display: grid; gap: 10px; } article { display: flex; justify-content: space-between; gap: 16px; align-items: start; padding: 14px 0; border-top: 1px solid var(--border-dim); } article div { display: grid; gap: 4px; min-width: 0; } article strong,small { overflow-wrap: anywhere; } button { background: var(--bg-secondary); color: var(--text-primary); border: 1px solid var(--border-default); border-radius: var(--radius-sm); padding: 9px 11px; cursor: pointer; } button:disabled { opacity: .5; cursor: not-allowed; } .panel > button { margin-top: 16px; } .edges,.impact div { margin-top: 16px; } h3 { font-size: 14px; margin-bottom: 8px; } .edges p,.impact p { overflow-wrap: anywhere; padding: 5px 0; } .edges span,.impact span { margin-left: 8px; }
@media (max-width: 700px) { .change-quality { padding: 24px 16px; } header,article { flex-direction: column; } .generations { grid-template-columns: 1fr; } }
</style>
