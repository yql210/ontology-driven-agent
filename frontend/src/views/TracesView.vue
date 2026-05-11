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
      renderMermaid()
    } catch (e) {
      console.error('Failed to load mermaid:', e)
    }
  } else {
    renderMermaid()
  }
}

function renderMermaid() {
  // Dynamic import mermaid to avoid SSR issues
  import('mermaid').then((mermaid) => {
    mermaid.default.initialize({ startOnLoad: false, theme: 'dark' })
    mermaid.default.contentLoaded()
  })
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
          <div class="modal-body">
            <pre class="mermaid">{{ mermaidCode }}</pre>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.traces-view {
  padding: 20px;
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
  color: #ecf0f1;
}

.btn-graph {
  padding: 8px 16px;
  background: #3498db;
  color: white;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  font-size: 14px;
}

.btn-graph:hover {
  background: #2980b9;
}

.loading, .empty-state {
  text-align: center;
  padding: 60px 20px;
  color: #95a5a6;
}

.traces-table {
  width: 100%;
  border-collapse: collapse;
  background: #34495e;
  border-radius: 8px;
  overflow: hidden;
}

.traces-table th,
.traces-table td {
  padding: 12px 16px;
  text-align: left;
  border-bottom: 1px solid #2c3e50;
}

.traces-table th {
  background: #2c3e50;
  color: #ecf0f1;
  font-weight: 600;
}

.traces-table tr:last-child td {
  border-bottom: none;
}

.trace-row {
  cursor: pointer;
  transition: background 0.2s;
}

.trace-row:hover {
  background: #3d566e;
}

.query-cell {
  color: #ecf0f1;
  max-width: 300px;
}

.time-cell {
  color: #95a5a6;
  font-size: 13px;
}

.badge {
  display: inline-block;
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 500;
}

.badge-success {
  background: #27ae60;
  color: white;
}

.badge-running {
  background: #f39c12;
  color: white;
}

.badge-error {
  background: #e74c3c;
  color: white;
}

.badge-default {
  background: #7f8c8d;
  color: white;
}

/* Modal */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.7);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal-content {
  background: #2c3e50;
  border-radius: 8px;
  width: 90%;
  max-width: 900px;
  max-height: 80vh;
  overflow: hidden;
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  border-bottom: 1px solid #34495e;
}

.modal-header h2 {
  margin: 0;
  color: #ecf0f1;
}

.close-btn {
  background: none;
  border: none;
  color: #95a5a6;
  font-size: 24px;
  cursor: pointer;
  padding: 0;
  line-height: 1;
}

.close-btn:hover {
  color: #ecf0f1;
}

.modal-body {
  padding: 20px;
  overflow: auto;
  max-height: calc(80vh - 60px);
}

.mermaid {
  display: flex;
  justify-content: center;
  background: #1a252f;
  padding: 20px;
  border-radius: 4px;
}
</style>
