<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getTrace, getMermaid, deleteTrace } from '../api/trace'
import type { TraceInfo } from '../api/types'

const route = useRoute()
const router = useRouter()
const threadId = computed(() => route.params.threadId as string)

const trace = ref<TraceInfo | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)
const mermaidCode = ref('')
const showMermaid = ref(false)
const expandedSteps = ref<Set<number>>(new Set())
let pollTimer: number | null = null

async function loadTrace() {
  try {
    trace.value = await getTrace(threadId.value)
    error.value = null
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

function startPolling() {
  if (pollTimer) return
  pollTimer = window.setInterval(() => {
    if (trace.value?.status === 'running') {
      loadTrace()
    } else {
      stopPolling()
    }
  }, 2000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
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
  import('mermaid').then((m) => {
    m.default.initialize({ startOnLoad: false, theme: 'dark' })
    m.default.contentLoaded()
  })
}

function toggleExpand(stepId: number) {
  if (expandedSteps.value.has(stepId)) {
    expandedSteps.value.delete(stepId)
  } else {
    expandedSteps.value.add(stepId)
  }
}

function isExpanded(stepId: number): boolean {
  return expandedSteps.value.has(stepId)
}

function stepIcon(type: string): string {
  switch (type) {
    case 'thinking': return '🧠'
    case 'tool_call': return '🔧'
    case 'tool_result': return '📋'
    case 'final': return '✅'
    default: return '•'
  }
}

function formatDuration(ms?: number): string {
  if (!ms) return ''
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

async function handleDelete() {
  if (!confirm('确定删除这个 Trace？')) return
  try {
    await deleteTrace(threadId.value)
    router.push('/traces')
  } catch (e) {
    alert('删除失败: ' + (e as Error).message)
  }
}

onMounted(() => {
  loadTrace()
  startPolling()
})

onUnmounted(() => {
  stopPolling()
})
</script>

<template>
  <div class="trace-detail-view">
    <header class="detail-header">
      <div class="header-left">
        <button class="back-btn" @click="router.push('/traces')">← 返回</button>
        <div v-if="trace" class="header-info">
          <h1>{{ trace.query }}</h1>
          <span :class="['badge', 'badge-large', statusBadge(trace.status).class]">
            {{ statusBadge(trace.status).label }}
          </span>
          <span v-if="trace.total_duration_ms" class="duration">
            耗时: {{ formatDuration(trace.total_duration_ms) }}
          </span>
        </div>
      </div>
      <div class="header-actions">
        <button class="btn-graph" @click="showAgentGraph">Agent 图</button>
        <button class="btn-delete" @click="handleDelete">删除</button>
      </div>
    </header>

    <div v-if="loading" class="loading">加载中...</div>

    <div v-else-if="error" class="error">{{ error }}</div>

    <div v-else-if="trace" class="trace-content">
      <!-- Mermaid 图 -->
      <section class="mermaid-section">
        <h3>Agent 执行流程</h3>
        <div class="mermaid-container">
          <pre class="mermaid">{{ mermaidCode || '加载中...' }}</pre>
        </div>
      </section>

      <!-- 时间线 -->
      <section class="timeline-section">
        <h3>执行步骤</h3>
        <div class="timeline">
          <div
            v-for="step in trace.steps"
            :key="step.step_id"
            class="timeline-item"
            :class="[`type-${step.type}`]"
          >
            <div class="timeline-marker">{{ stepIcon(step.type) }}</div>
            <div class="timeline-content">
              <div class="step-header">
                <span class="step-type">{{ step.type }}</span>
                <span v-if="step.duration_ms" class="step-duration">{{ formatDuration(step.duration_ms) }}</span>
              </div>
              <div class="step-content">{{ step.content }}</div>
              <div v-if="step.tool_name" class="tool-name">
                工具: <code>{{ step.tool_name }}</code>
              </div>
              <div v-if="step.tool_args" class="tool-args">
                <button class="toggle-btn" @click="toggleExpand(step.step_id)">
                  {{ isExpanded(step.step_id) ? '▼ 隐藏参数' : '▶ 查看参数' }}
                </button>
                <pre v-if="isExpanded(step.step_id)" class="code-block">{{ step.tool_args }}</pre>
              </div>
              <div v-if="step.tool_result" class="tool-result">
                <button class="toggle-btn" @click="toggleExpand(step.step_id + 1000)">
                  {{ isExpanded(step.step_id + 1000) ? '▼ 隐藏结果' : '▶ 查看结果' }}
                </button>
                <pre v-if="isExpanded(step.step_id + 1000)" class="code-block">{{ step.tool_result }}</pre>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>

    <!-- Mermaid Modal -->
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
.trace-detail-view {
  padding: 20px;
  max-width: 1200px;
  margin: 0 auto;
}

.detail-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 24px;
  padding-bottom: 16px;
  border-bottom: 1px solid #34495e;
}

.header-left {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.back-btn {
  padding: 6px 12px;
  background: #34495e;
  color: #ecf0f1;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  font-size: 14px;
  align-self: flex-start;
}

.back-btn:hover {
  background: #2c3e50;
}

.header-info {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.header-info h1 {
  margin: 0;
  color: #ecf0f1;
  font-size: 20px;
}

.header-actions {
  display: flex;
  gap: 8px;
}

.btn-graph {
  padding: 8px 16px;
  background: #3498db;
  color: white;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}

.btn-graph:hover {
  background: #2980b9;
}

.btn-delete {
  padding: 8px 16px;
  background: #e74c3c;
  color: white;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}

.btn-delete:hover {
  background: #c0392b;
}

.loading, .error {
  text-align: center;
  padding: 60px;
  color: #95a5a6;
}

.error {
  color: #e74c3c;
}

.badge {
  display: inline-block;
  padding: 4px 10px;
  border-radius: 4px;
  font-size: 13px;
  font-weight: 500;
}

.badge-large {
  padding: 6px 12px;
  font-size: 14px;
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

.duration {
  color: #95a5a6;
  font-size: 14px;
}

/* Sections */
.trace-content {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.mermaid-section,
.timeline-section {
  background: #34495e;
  border-radius: 8px;
  padding: 20px;
}

.mermaid-section h3,
.timeline-section h3 {
  margin: 0 0 16px;
  color: #ecf0f1;
  font-size: 18px;
}

.mermaid-container {
  background: #1a252f;
  padding: 16px;
  border-radius: 4px;
  overflow-x: auto;
}

.mermaid-code {
  margin: 0;
  color: #ecf0f1;
  font-size: 13px;
  white-space: pre-wrap;
}

/* Timeline */
.timeline {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.timeline-item {
  display: flex;
  gap: 12px;
  position: relative;
}

.timeline-item:not(:last-child)::after {
  content: '';
  position: absolute;
  left: 16px;
  top: 40px;
  bottom: -16px;
  width: 2px;
  background: #4a5f7a;
}

.timeline-marker {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: #2c3e50;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
  z-index: 1;
}

.timeline-item.type-thinking .timeline-marker {
  background: #9b59b6;
}

.timeline-item.type-tool_call .timeline-marker {
  background: #e67e22;
}

.timeline-item.type-tool_result .timeline-marker {
  background: #3498db;
}

.timeline-item.type-final .timeline-marker {
  background: #27ae60;
}

.timeline-content {
  flex: 1;
  background: #2c3e50;
  padding: 12px 16px;
  border-radius: 6px;
}

.step-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 8px;
}

.step-type {
  font-weight: 600;
  color: #ecf0f1;
  text-transform: capitalize;
}

.step-duration {
  color: #95a5a6;
  font-size: 13px;
}

.step-content {
  color: #ecf0f1;
  margin-bottom: 8px;
  line-height: 1.5;
}

.tool-name {
  color: #95a5a6;
  font-size: 13px;
  margin-top: 8px;
}

.tool-name code {
  background: #1a252f;
  padding: 2px 6px;
  border-radius: 3px;
  color: #3498db;
  font-family: 'Consolas', 'Monaco', monospace;
}

.toggle-btn {
  background: none;
  border: none;
  color: #3498db;
  cursor: pointer;
  font-size: 12px;
  padding: 4px 0;
  margin-top: 8px;
}

.toggle-btn:hover {
  text-decoration: underline;
}

.code-block {
  background: #1a252f;
  padding: 12px;
  border-radius: 4px;
  margin: 8px 0 0;
  overflow-x: auto;
  font-size: 12px;
  color: #ecf0f1;
  white-space: pre-wrap;
  word-break: break-all;
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
