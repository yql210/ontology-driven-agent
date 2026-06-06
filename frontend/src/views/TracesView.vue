<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { listTraces, getMermaid } from '../api/trace'
import type { TraceListItem } from '../api/types'

const router = useRouter()
const traces = ref<TraceListItem[]>([])
const loading = ref(true)
const showMermaid = ref(false)
const mermaidCode = ref('')
const mermaidSvg = ref('')
let mermaidInitialized = false

async function loadTraces() {
  loading.value = true
  try {
    traces.value = await listTraces()
  } catch (e) {
    console.error('Failed to load traces:', e)
  } finally {
    loading.value = false
  }
}

async function showAgentGraph() {
  showMermaid.value = true
  if (!mermaidCode.value) {
    try {
      mermaidCode.value = await getMermaid()
    } catch (e) {
      console.error('Failed to load mermaid:', e)
      return
    }
  }
  await renderMermaid()
}

async function renderMermaid() {
  try {
    const mermaid = await import('mermaid')
    if (!mermaidInitialized) {
      mermaid.default.initialize({ startOnLoad: false, theme: 'dark' })
      mermaidInitialized = true
    }
    const id = `mermaid-${Date.now()}`
    const { svg } = await mermaid.default.render(id, mermaidCode.value)
    mermaidSvg.value = svg
  } catch (e) {
    console.error('Mermaid render error:', e)
    mermaidSvg.value = '<p class="mermaid-error">流程图渲染失败</p>'
  }
}

function formatTime(ms: number): string {
  return new Date(ms * 1000).toLocaleString('zh-CN')
}

function formatDuration(ms?: number): string {
  if (!ms) return '-'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

function statusBadge(status: string): { class: string; label: string } {
  switch (status) {
    case 'completed':
      return { class: 'badge-success', label: '✅ 完成' }
    case 'running':
      return { class: 'badge-running', label: '🔄 运行中' }
    case 'failed':
      return { class: 'badge-error', label: '❌ 失败' }
    default:
      return { class: 'badge-default', label: status }
  }
}

onMounted(() => {
  loadTraces()
})
</script>

<template>
  <div class="traces-view">
    <header class="traces-header">
      <h1>📊 Traces</h1>
      <button class="btn-graph" @click="showAgentGraph">Agent 图结构</button>
    </header>

    <div v-if="loading" class="loading">加载中...</div>

    <div v-else-if="traces.length === 0" class="empty-state">
      <p>暂无 Trace 记录</p>
    </div>

    <table v-else class="traces-table">
      <thead>
        <tr>
          <th>Query</th>
          <th>状态</th>
          <th>步骤数</th>
          <th>耗时</th>
          <th>创建时间</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="trace in traces" :key="trace.thread_id" class="trace-row" @click="router.push(`/traces/${trace.thread_id}`)">
          <td class="query-cell">{{ trace.query.slice(0, 60) }}{{ trace.query.length > 60 ? '...' : '' }}</td>
          <td><span :class="['badge', statusBadge(trace.status).class]">{{ statusBadge(trace.status).label }}</span></td>
          <td>{{ trace.step_count }}</td>
          <td>{{ formatDuration(trace.total_duration_ms) }}</td>
          <td class="time-cell">{{ formatTime(trace.created_at) }}</td>
        </tr>
      </tbody>
    </table>

    <Teleport to="body">
      <div v-if="showMermaid" class="modal-overlay" @click.self="showMermaid = false">
        <div class="modal-content">
          <header class="modal-header">
            <h2>Agent Graph</h2>
            <button class="close-btn" @click="showMermaid = false">&times;</button>
          </header>
          <div class="modal-body" v-html="mermaidSvg"></div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.traces-view {
  padding: 24px;
  max-width: 1200px;
  margin: 0 auto;
}

.traces-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.traces-header h1 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  background: linear-gradient(135deg, #a78bfa, #60a5fa);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.btn-graph {
  padding: 8px 16px;
  background: linear-gradient(135deg, #8b5cf6, #3b82f6);
  color: #fff;
  border: none;
  border-radius: var(--radius-pill);
  cursor: pointer;
  font-size: 14px;
  font-weight: 500;
  font-family: var(--font-sans);
  transition: box-shadow var(--transition-fast);
}
.btn-graph:hover {
  box-shadow: 0 0 12px rgba(139,92,246,0.25), 0 0 24px rgba(139,92,246,0.1);
}

.loading, .empty-state {
  text-align: center;
  padding: 60px 20px;
  color: var(--text-muted);
}

.traces-table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0 8px;
}

.traces-table th {
  padding: 12px 16px;
  text-align: left;
  color: var(--text-muted);
  font-weight: 500;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.traces-table td {
  padding: 16px;
  background: var(--bg-card);
  border-top: 1px solid var(--border-dim);
  border-bottom: 1px solid var(--border-dim);
}
.traces-table td:first-child {
  border-left: 1px solid var(--border-dim);
  border-radius: var(--radius-md) 0 0 var(--radius-md);
}
.traces-table td:last-child {
  border-right: 1px solid var(--border-dim);
  border-radius: 0 var(--radius-md) var(--radius-md) 0;
}

.trace-row {
  cursor: pointer;
  transition: all var(--transition-normal);
}
.trace-row td {
  transition: border-color var(--transition-normal), background var(--transition-normal);
}
.trace-row:hover td {
  border-color: var(--border-default);
  background: rgba(30,41,59,0.95);
}

.query-cell {
  color: var(--text-primary);
  max-width: 300px;
}

.time-cell {
  color: var(--text-muted);
  font-size: 13px;
}

.badge {
  display: inline-block;
  padding: 4px 10px;
  border-radius: var(--radius-pill);
  font-size: 12px;
  font-weight: 500;
}

.badge-success {
  background: rgba(52,211,153,0.15);
  color: #34d399;
}

.badge-running {
  background: rgba(251,191,36,0.15);
  color: #fbbf24;
}

.badge-error {
  background: rgba(248,113,113,0.15);
  color: #f87171;
}

.badge-default {
  background: rgba(148,163,184,0.15);
  color: var(--text-secondary);
}

/* Modal */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.7);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal-content {
  background: var(--bg-secondary);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg);
  width: 90%;
  max-width: 900px;
  max-height: 80vh;
  overflow: hidden;
  box-shadow: 0 8px 32px rgba(0,0,0,0.4);
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border-dim);
}

.modal-header h2 {
  margin: 0;
  color: var(--text-primary);
  font-weight: 600;
}

.close-btn {
  background: none;
  border: none;
  color: var(--text-muted);
  font-size: 24px;
  cursor: pointer;
  padding: 0;
  line-height: 1;
  transition: color var(--transition-fast);
}
.close-btn:hover {
  color: var(--text-primary);
}

.modal-body {
  padding: 20px;
  overflow: auto;
  max-height: calc(80vh - 60px);
  background: var(--bg-primary);
}

.modal-body :deep(svg) {
  max-width: 100%;
  height: auto;
}

.mermaid-error {
  color: #f87171;
  text-align: center;
  padding: 40px;
}
</style>
