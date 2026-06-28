<script setup lang="ts">
export interface ConstraintCheckData {
  pass: boolean
  checks: Array<{ guard: string; level: string; reason: string }>
  target?: string
  block_reason?: string
}

const props = defineProps<{ checkResult: ConstraintCheckData }>()

function getLevelBadge(level: string) {
  switch (level) {
    case 'block':
      return '🔴 BLOCK'
    case 'warn':
      return '🟡 WARN'
    case 'allow':
      return '🟢 ALLOW'
    default:
      return level.toUpperCase()
  }
}

function getLevelClass(level: string) {
  return `level-${level}`
}
</script>

<template>
  <div class="check-card" :class="{ pass: checkResult.pass }">
    <div class="cc-header">
      <span class="cc-icon">{{ checkResult.pass ? '✅' : '⚠️' }}</span>
      <span class="cc-title">{{ checkResult.pass ? '可通过' : '存在约束' }}</span>
      <span v-if="checkResult.target" class="cc-target">{{ checkResult.target }}</span>
    </div>

    <div v-if="checkResult.block_reason" class="cc-block-reason">
      {{ checkResult.block_reason }}
    </div>

    <div class="cc-checks">
      <div
        v-for="(c, i) in checkResult.checks"
        :key="i"
        class="cc-row"
        :class="getLevelClass(c.level)"
      >
        <span class="cc-guard">{{ c.guard }}</span>
        <span class="cc-badge">{{ getLevelBadge(c.level) }}</span>
        <span class="cc-reason">{{ c.reason.slice(0, 80) }}{{ c.reason.length > 80 ? '…' : '' }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.check-card {
  border: 2px solid #10b981;
  border-radius: 12px;
  background: rgba(16, 185, 129, 0.05);
  backdrop-filter: blur(8px);
  margin: 12px 0;
  overflow: hidden;
  animation: slide-up 0.3s ease-out;
}
.check-card:not(.pass) {
  border-color: #f59e0b;
  background: rgba(245, 158, 11, 0.05);
}
.cc-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 14px 16px;
  background: rgba(16, 185, 129, 0.1);
  border-bottom: 1px solid rgba(16, 185, 129, 0.2);
}
.check-card:not(.pass) .cc-header {
  background: rgba(245, 158, 11, 0.1);
  border-bottom-color: rgba(245, 158, 11, 0.2);
}
.cc-icon {
  font-size: 1.2em;
}
.cc-title {
  font-weight: 600;
  font-size: 1.05em;
  color: #34d399;
}
.check-card:not(.pass) .cc-title {
  color: #fbbf24;
}
.cc-target {
  margin-left: auto;
  font-size: 0.8em;
  color: #6b7280;
  background: rgba(255, 255, 255, 0.05);
  padding: 2px 8px;
  border-radius: 8px;
}
.cc-block-reason {
  padding: 10px 16px;
  font-size: 0.85em;
  color: #fca5a5;
  background: rgba(239, 68, 68, 0.08);
  border-bottom: 1px solid rgba(239, 68, 68, 0.15);
}
.cc-checks {
  padding: 4px 0;
}
.cc-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  font-size: 0.85em;
  border-top: 1px solid rgba(255, 255, 255, 0.05);
}
.cc-row.level-block {
  background: rgba(239, 68, 68, 0.06);
}
.cc-row.level-warn {
  background: rgba(245, 158, 11, 0.04);
}
.cc-guard {
  font-weight: 500;
  min-width: 160px;
  color: #e5e7eb;
}
.cc-badge {
  font-size: 0.8em;
  padding: 1px 6px;
  border-radius: 4px;
}
.cc-reason {
  color: #9ca3af;
  flex: 1;
}
@keyframes slide-up {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
