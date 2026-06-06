<script setup lang="ts">
import { ref } from 'vue'
import type { ToolCall } from '../api/types'

const props = defineProps<{ toolCall: ToolCall }>()
const expanded = ref(false)
</script>

<template>
  <div class="tool-call" :class="toolCall.status">
    <div class="tool-header" @click="expanded = !expanded">
      <span class="tool-icon">🔧</span>
      <span class="tool-name">{{ toolCall.tool }}</span>
      <span class="tool-status">{{ toolCall.status === 'running' ? '⏳' : '✅' }}</span>
      <span class="tool-toggle">{{ expanded ? '▾' : '▸' }}</span>
    </div>
    <div v-if="expanded" class="tool-body">
      <div v-if="toolCall.args" class="tool-args">
        <div class="section-label">参数</div>
        <pre>{{ JSON.stringify(toolCall.args, null, 2) }}</pre>
      </div>
      <div v-if="toolCall.result" class="tool-result">
        <div class="section-label">结果</div>
        <pre>{{ toolCall.result }}</pre>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tool-call {
  border-left: 3px solid transparent;
  border-image: linear-gradient(135deg, #8b5cf6, #3b82f6) 1;
  margin: 8px 0;
  border-radius: var(--radius-md);
  background: var(--bg-glass);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  border: 1px solid var(--border-dim);
  border-left: 3px solid transparent;
  border-image: linear-gradient(135deg, #8b5cf6, #3b82f6) 1;
  overflow: hidden;
}
.tool-header {
  padding: 10px 14px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text-secondary);
  transition: color var(--transition-fast), background var(--transition-fast);
}
.tool-header:hover {
  color: var(--text-primary);
  background: rgba(148,163,184,0.05);
}
.tool-toggle {
  margin-left: auto;
  transition: transform var(--transition-fast);
}
.tool-body {
  display: flex;
  flex-direction: column;
  padding: 0 14px 14px;
  font-size: 13px;
  color: var(--text-secondary);
}
.tool-args pre {
  margin: 0;
  padding: 10px 12px;
  background: var(--bg-primary);
  color: #d4d4d4;
  font-size: 0.8em;
  font-family: var(--font-mono);
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-dim);
}
.tool-result {
  margin-top: 4px;
}
.tool-result pre {
  margin: 0;
  padding: 10px 12px;
  background: var(--bg-primary);
  color: #d4d4d4;
  font-size: 0.8em;
  font-family: var(--font-mono);
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-dim);
}
.section-label {
  padding: 4px 0 6px;
  font-size: 0.75em;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  font-weight: 500;
}
</style>
