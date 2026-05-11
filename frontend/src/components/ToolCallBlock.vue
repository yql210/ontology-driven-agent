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
    <div v-if="expanded && toolCall.args" class="tool-args">
      <pre>{{ JSON.stringify(toolCall.args, null, 2) }}</pre>
    </div>
  </div>
</template>

<style scoped>
.tool-call {
  border-left: 3px solid #4a9eff;
  margin: 4px 0;
  border-radius: 4px;
  background: #f8f9fa;
}
.tool-call.running { border-left-color: #f0ad4e; }
.tool-call.completed { border-left-color: #5cb85c; }
.tool-header {
  padding: 6px 10px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.85em;
}
.tool-args pre {
  margin: 0;
  padding: 8px 10px;
  background: #1e1e2e;
  color: #d4d4d4;
  font-size: 0.8em;
  border-radius: 0 0 4px 4px;
}
</style>
