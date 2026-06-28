<script setup lang="ts">
import { ref } from 'vue'

export interface ApprovalData {
  approval_id: string
  level: string
  checks?: Array<{ guard: string; level: string; reason: string }>
  policies?: Array<{ policy: string; level: string; reason: string }>
  summary?: string
}

const props = defineProps<{ approval: ApprovalData; disabled?: boolean }>()
const emit = defineEmits<{ approve: [approvalId: string]; reject: [approvalId: string] }>()
const resolved = ref(false)

function getLevelBadge(level: string) {
  switch (level) {
    case 'block':
      return '🔴 BLOCK'
    case 'warn':
      return '🟡 WARN'
    case 'allow':
      return '🟢 ALLOW'
    case 'pending':
      return '🟠 PENDING'
    default:
      return level.toUpperCase()
  }
}

function getLevelClass(level: string) {
  return `level-${level}`
}

function onApprove() {
  resolved.value = true
  emit('approve', props.approval.approval_id)
}

function onReject() {
  resolved.value = true
  emit('reject', props.approval.approval_id)
}
</script>

<template>
  <div class="approval-card" :class="{ resolved }">
    <div class="ac-header">
      <span class="ac-icon">🛡️</span>
      <span class="ac-title">操作审批</span>
      <span class="ac-level">{{ approval.level === 'action' ? '操作级别' : '函数级别' }}</span>
    </div>

    <div class="ac-summary" v-if="approval.summary">
      {{ approval.summary }}
    </div>

    <!-- 约束检查结果 -->
    <div class="ac-checks" v-if="approval.checks?.length">
      <div class="ac-section-title">约束检查</div>
      <div
        v-for="(check, i) in approval.checks"
        :key="i"
        class="check-row"
        :class="getLevelClass(check.level)"
      >
        <span class="check-guard">{{ check.guard }}</span>
        <span class="check-badge">{{ getLevelBadge(check.level) }}</span>
        <span class="check-reason">{{ check.reason }}</span>
      </div>
    </div>

    <!-- 策略结果 -->
    <div class="ac-policies" v-if="approval.policies?.length">
      <div class="ac-section-title">审批策略</div>
      <div v-for="(p, i) in approval.policies" :key="i" class="policy-row">
        <span class="policy-name">{{ p.policy }}</span>
        <span class="policy-level">{{ p.level }}</span>
      </div>
    </div>

    <div class="ac-token">
      <span class="token-label">审批令牌</span>
      <code class="token-value">{{ approval.approval_id }}</code>
    </div>

    <div class="ac-actions" v-if="!resolved">
      <button class="btn-approve" :disabled="disabled" @click="onApprove">✅ 批准执行</button>
      <button class="btn-reject" :disabled="disabled" @click="onReject">❌ 拒绝</button>
    </div>
    <div class="ac-resolved" v-else>
      <span>⏳ 处理中...</span>
    </div>
  </div>
</template>

<style scoped>
.approval-card {
  border: 2px solid #f59e0b;
  border-radius: 12px;
  background: rgba(245, 158, 11, 0.05);
  backdrop-filter: blur(8px);
  margin: 12px 0;
  overflow: hidden;
  animation: slide-up 0.3s ease-out;
}
.approval-card.resolved {
  border-color: #6b7280;
  opacity: 0.7;
}
.ac-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 14px 16px;
  background: rgba(245, 158, 11, 0.1);
  border-bottom: 1px solid rgba(245, 158, 11, 0.2);
}
.ac-icon {
  font-size: 1.2em;
}
.ac-title {
  font-weight: 600;
  font-size: 1.05em;
  color: #fbbf24;
}
.ac-level {
  margin-left: auto;
  font-size: 0.8em;
  color: #d97706;
  background: rgba(245, 158, 11, 0.15);
  padding: 2px 8px;
  border-radius: 8px;
}
.ac-summary {
  padding: 12px 16px;
  font-size: 0.9em;
  color: #d1d5db;
}
.ac-section-title {
  padding: 6px 16px;
  font-size: 0.75em;
  color: #9ca3af;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.check-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  font-size: 0.85em;
  border-top: 1px solid rgba(255, 255, 255, 0.05);
}
.check-row.level-block {
  background: rgba(239, 68, 68, 0.06);
}
.check-row.level-warn {
  background: rgba(245, 158, 11, 0.04);
}
.check-guard {
  font-weight: 500;
  min-width: 160px;
  color: #e5e7eb;
}
.check-badge {
  font-size: 0.8em;
  padding: 1px 6px;
  border-radius: 4px;
}
.check-reason {
  color: #9ca3af;
  flex: 1;
}
.policy-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 16px;
  font-size: 0.8em;
  color: #9ca3af;
}
.ac-token {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  border-top: 1px solid rgba(255, 255, 255, 0.05);
}
.token-label {
  font-size: 0.75em;
  color: #6b7280;
}
.token-value {
  font-size: 0.8em;
  font-family: monospace;
  color: #93c5fd;
  background: rgba(59, 130, 246, 0.1);
  padding: 2px 8px;
  border-radius: 4px;
}
.ac-actions {
  display: flex;
  gap: 10px;
  padding: 14px 16px;
  border-top: 1px solid rgba(245, 158, 11, 0.2);
}
.btn-approve,
.btn-reject {
  flex: 1;
  padding: 10px;
  border: none;
  border-radius: 8px;
  font-size: 0.9em;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
}
.btn-approve {
  background: linear-gradient(135deg, #10b981, #059669);
  color: #fff;
}
.btn-approve:hover {
  background: linear-gradient(135deg, #34d399, #10b981);
}
.btn-reject {
  background: rgba(239, 68, 68, 0.2);
  color: #fca5a5;
  border: 1px solid rgba(239, 68, 68, 0.3);
}
.btn-reject:hover {
  background: rgba(239, 68, 68, 0.35);
}
.btn-approve:disabled,
.btn-reject:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.ac-resolved {
  padding: 14px 16px;
  color: #9ca3af;
  font-size: 0.9em;
  text-align: center;
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
