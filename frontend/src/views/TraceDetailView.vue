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
const mermaidSvg = ref('')
const showMermaid = ref(false)
const expandedSteps = ref<Set<number>>(new Set())
let pollTimer: number | null = null
let mermaidInitialized = false

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
    const id = `mermaid-detail-${Date.now()}`
    const { svg } = await mermaid.default.render(id, mermaidCode.value)
    mermaidSvg.value = svg
  } catch (e) {
    console.error('Mermaid render error:', e)
    mermaidSvg.value = '<p class="mermaid-error">流程图渲染失败</p>'
  }
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
          <div class="modal-body" v-html="mermaidSvg"></div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.trace-detail-view {
  padding: 24px;
  max-width: 900px;
  margin: 0 auto;
}

.detail-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 24px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border-dim);
}

.header-left {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.back-btn {
  padding: 6px 14px;
  background: var(--bg-tertiary);
  color: var(--text-secondary);
  border: 1px solid var(--border-dim);
  border-radius: var(--radius-pill);
  cursor: pointer;
  font-size: 14px;
  font-family: var(--font-sans);
  align-self: flex-start;
  transition: color var(--transition-fast), border-color var(--transition-fast), box-shadow var(--transition-fast);
}
.back-btn:hover {
  color: var(--text-primary);
  border-color: var(--border-default);
  box-shadow: var(--glow-xs);
}

.header-info {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.header-info h1 {
  margin: 0;
  color: var(--text-primary);
  font-size: 20px;
  font-weight: 600;
}

.header-actions {
  display: flex;
  gap: 8px;
}

.btn-graph {
  padding: 6px 14px;
  background: linear-gradient(135deg, #8b5cf6, #3b82f6);
  color: #fff;
  border: none;
  border-radius: var(--radius-pill);
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  font-family: var(--font-sans);
  transition: box-shadow var(--transition-fast), transform var(--transition-fast);
}
.btn-graph:hover {
  box-shadow: var(--glow-md);
  transform: translateY(-1px);
}

.btn-delete {
  padding: 6px 14px;
  background: rgba(248,113,113,0.15);
  color: #f87171;
  border: 1px solid rgba(248,113,113,0.2);
  border-radius: var(--radius-pill);
  cursor: pointer;
  font-size: 12px;
  font-family: var(--font-sans);
  transition: background var(--transition-fast), box-shadow var(--transition-fast), transform var(--transition-fast);
}
.btn-delete:hover {
  background: rgba(248,113,113,0.25);
  box-shadow: 0 0 12px rgba(248,113,113,0.2), 0 0 24px rgba(248,113,113,0.08);
  transform: translateY(-1px);
}

.loading, .error {
  text-align: center;
  padding: 60px;
  color: var(--text-muted);
}

.error {
  color: #f87171;
}

.badge {
  display: inline-block;
  padding: 4px 10px;
  border-radius: var(--radius-pill);
  font-size: 12px;
  font-weight: 500;
}

.badge-large {
  padding: 6px 12px;
  font-size: 14px;
}

.badge-success {
  background: rgba(52,211,153,0.15);
  color: #34d399;
  box-shadow: 0 0 6px rgba(52,211,153,0.15);
}

.badge-running {
  background: rgba(251,191,36,0.15);
  color: #fbbf24;
  box-shadow: 0 0 6px rgba(251,191,36,0.15);
}

.badge-error {
  background: rgba(248,113,113,0.15);
  color: #f87171;
  box-shadow: 0 0 6px rgba(248,113,113,0.15);
}

.badge-default {
  background: rgba(148,163,184,0.15);
  color: var(--text-secondary);
  box-shadow: 0 0 4px rgba(148,163,184,0.08);
}

.duration {
  color: var(--text-muted);
  font-size: 14px;
}

/* Sections */
.trace-content {
  display: flex;
  flex-direction: column;
  gap: 24px;
  animation: fadeIn 0.4s ease;
}

.mermaid-section,
.timeline-section {
  background: var(--bg-card);
  border: 1px solid var(--border-dim);
  border-radius: var(--radius-md);
  padding: 20px;
}

.mermaid-section h3,
.timeline-section h3 {
  margin: 0 0 16px;
  color: var(--text-primary);
  font-size: 18px;
  font-weight: 600;
  background: linear-gradient(135deg, #a78bfa, #60a5fa);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.mermaid-container {
  background: var(--bg-primary);
  padding: 16px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-dim);
  overflow-x: auto;
}

.mermaid-code {
  margin: 0;
  color: var(--text-primary);
  font-size: 13px;
  white-space: pre-wrap;
}

/* Timeline */
.timeline {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.timeline-item {
  display: flex;
  gap: 12px;
  position: relative;
  padding-left: 0;
  margin-bottom: 24px;
}

.timeline-item:last-child {
  margin-bottom: 0;
}

/* Timeline vertical line with gradient pulse animation */
.timeline-item:not(:last-child)::after {
  content: '';
  position: absolute;
  left: 16px;
  top: 40px;
  bottom: -24px;
  width: 2px;
  background: linear-gradient(180deg, #8b5cf6, #3b82f6);
  background-size: 100% 200%;
  animation: gradient-shift 3s ease infinite;
  opacity: 0.7;
}

/* Timeline marker with glow-pulse */
.timeline-marker {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: var(--bg-tertiary);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
  z-index: 1;
  animation: glow-pulse 3s ease-in-out infinite;
}

.timeline-item.type-thinking .timeline-marker {
  background: rgba(139,92,246,0.3);
  box-shadow: 0 0 8px rgba(139, 92, 246, 0.15);
}

.timeline-item.type-tool_call .timeline-marker {
  background: rgba(251,191,36,0.3);
  box-shadow: 0 0 8px rgba(251, 191, 36, 0.15);
}

.timeline-item.type-tool_result .timeline-marker {
  background: rgba(96,165,250,0.3);
  box-shadow: 0 0 8px rgba(96, 165, 250, 0.15);
}

.timeline-item.type-final .timeline-marker {
  background: rgba(52,211,153,0.3);
  box-shadow: 0 0 8px rgba(52, 211, 153, 0.15);
}

.timeline-content {
  flex: 1;
  background: var(--bg-card);
  border: 1px solid var(--border-dim);
  padding: 16px;
  border-radius: var(--radius-md);
  transition: border-color var(--transition-normal), box-shadow var(--transition-normal);
}
.timeline-content:hover {
  border-color: var(--border-default);
  box-shadow: var(--glow-xs);
}

.step-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 8px;
}

.step-type {
  font-weight: 600;
  color: var(--text-primary);
  text-transform: capitalize;
}

.step-duration {
  color: var(--text-muted);
  font-size: 13px;
}

.step-content {
  color: var(--text-primary);
  margin-bottom: 8px;
  line-height: 1.5;
}

.tool-name {
  color: var(--text-muted);
  font-size: 13px;
  margin-top: 8px;
}

.tool-name code {
  background: var(--bg-primary);
  padding: 2px 6px;
  border-radius: 3px;
  color: var(--primary-light);
  font-family: var(--font-mono);
}

.toggle-btn {
  background: none;
  border: none;
  color: var(--primary-light);
  cursor: pointer;
  font-size: 12px;
  padding: 4px 0;
  margin-top: 8px;
  font-family: var(--font-sans);
  transition: color var(--transition-fast);
}
.toggle-btn:hover {
  color: #fff;
}

.code-block {
  background: var(--bg-primary);
  padding: 12px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-dim);
  margin: 8px 0 0;
  overflow-x: auto;
  font-size: 12px;
  font-family: var(--font-mono);
  color: var(--text-primary);
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
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

/* Modal with border glow */
.modal-content {
  background: var(--bg-secondary);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg);
  width: 90%;
  max-width: 900px;
  max-height: 80vh;
  overflow: hidden;
  box-shadow: 0 8px 32px rgba(0,0,0,0.4), 0 0 1px rgba(139,92,246,0.3), 0 0 16px rgba(139,92,246,0.1);
  animation: slide-up 0.3s var(--ease-spring);
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
