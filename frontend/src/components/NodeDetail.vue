<script setup lang="ts">
import type { NodeDetail } from '../api/types'

const props = defineProps<{
  node: NodeDetail | null
}>()

const emit = defineEmits<{
  expand: [name: string]
  delete: [id: string]
}>()

function handleDelete() {
  if (props.node && confirm(`确定要删除节点 "${props.node.name}" 吗？此操作不可恢复。`)) {
    emit('delete', props.node.id)
  }
}
</script>

<template>
  <div class="node-detail">
    <div v-if="!node" class="empty-state">
      <p>点击节点查看详情</p>
    </div>
    <div v-else class="detail-content">
      <div class="detail-header">
        <h3>{{ node.name }}</h3>
        <span class="node-type">{{ node.neo4jLabel }}</span>
      </div>

      <div class="detail-section">
        <h4>属性</h4>
        <div v-if="Object.keys(node.properties).length === 0" class="empty-hint">暂无属性</div>
        <div v-else class="property-list">
          <div v-for="[key, value] of Object.entries(node.properties)" :key="key" class="property-item">
            <span class="prop-key">{{ key }}</span>
            <span class="prop-value">{{ String(value) }}</span>
          </div>
        </div>
      </div>

      <div class="detail-section">
        <h4>入边</h4>
        <div v-if="node.relations.incoming.length === 0" class="empty-hint">无入边</div>
        <div v-else class="relation-list">
          <div v-for="r of node.relations.incoming" :key="r.source_id" class="relation-item">
            <span class="rel-source">{{ r.source_name }}</span>
            <span class="rel-type">--{{ r.type }}--&gt;</span>
          </div>
        </div>
      </div>

      <div class="detail-section">
        <h4>出边</h4>
        <div v-if="node.relations.outgoing.length === 0" class="empty-hint">无出边</div>
        <div v-else class="relation-list">
          <div v-for="r of node.relations.outgoing" :key="r.target_id" class="relation-item">
            <span class="rel-type">--{{ r.type }}--&gt;</span>
            <span class="rel-target">{{ r.target_name }}</span>
          </div>
        </div>
      </div>

      <div class="detail-actions">
        <button class="btn-expand" @click="emit('expand', node.name)">展开邻居</button>
        <button class="btn-delete" @click="handleDelete">删除</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.node-detail {
  height: 100%;
  width: 320px;
  background: var(--bg-secondary);
  border-left: 1px solid var(--border-dim);
  display: flex;
  flex-direction: column;
  position: relative;
}

.node-detail::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 2px;
  background: linear-gradient(180deg, #8b5cf6, #3b82f6, #34d399, #3b82f6, #8b5cf6);
  background-size: 100% 200%;
  animation: gradient-shift 4s ease infinite;
  box-shadow: 0 0 8px rgba(139,92,246,0.3), 0 0 20px rgba(139,92,246,0.1);
  z-index: 1;
}

.empty-state {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
  font-size: 14px;
}

.detail-content {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  animation: slide-up 0.3s var(--ease-smooth);
}

.detail-header {
  margin-bottom: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border-dim);
}

.detail-header h3 {
  margin: 0 0 12px 0;
  font-size: 16px;
  font-weight: 600;
  background: linear-gradient(135deg, #a78bfa, #60a5fa);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.node-type {
  display: inline-block;
  padding: 4px 10px;
  background: rgba(139,92,246,0.15);
  color: var(--primary-light);
  border-radius: var(--radius-pill);
  font-size: 12px;
  box-shadow: var(--glow-xs);
}

.detail-section {
  margin-bottom: 16px;
}

.detail-section h4 {
  margin: 0 0 8px 0;
  font-size: 14px;
  color: var(--text-secondary);
  font-weight: 500;
}

.empty-hint {
  color: var(--text-muted);
  font-size: 13px;
  font-style: italic;
}

.property-list,
.relation-list {
  border-radius: var(--radius-sm);
  padding: 4px;
}

.property-item {
  display: flex;
  justify-content: space-between;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  margin-bottom: 4px;
  transition: background var(--transition-fast);
  overflow: hidden;
}
.property-item:nth-child(even) {
  background: rgba(148,163,184,0.03);
}
.property-item:hover {
  background: rgba(139,92,246,0.05);
}

.prop-key {
  font-size: 12px;
  font-weight: 500;
  background: linear-gradient(135deg, #a78bfa, #60a5fa);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  overflow-wrap: break-word;
  word-break: break-word;
}

.prop-value {
  color: var(--text-primary);
  font-size: 13px;
  word-break: break-all;
}

.relation-item {
  padding: 4px 0;
  font-size: 13px;
  display: flex;
  align-items: center;
}

.rel-source,
.rel-target {
  color: var(--text-primary);
}

.rel-type {
  margin: 0 6px;
  font-family: var(--font-mono);
  font-size: 11px;
  padding: 2px 6px;
  border-radius: var(--radius-pill);
}

/* Relation type colors */
.rel-type {
  color: var(--primary-light);
  background: rgba(139,92,246,0.08);
}

.detail-actions {
  display: flex;
  gap: 8px;
  padding-top: 16px;
  border-top: 1px solid var(--border-dim);
}

.btn-expand,
.btn-delete {
  flex: 1;
  padding: 6px 14px;
  border: none;
  border-radius: var(--radius-pill);
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  font-family: var(--font-sans);
  transition: box-shadow var(--transition-fast), background var(--transition-fast), transform var(--transition-fast);
}

.btn-expand {
  background: linear-gradient(135deg, #8b5cf6, #3b82f6);
  color: #fff;
}
.btn-expand:hover {
  box-shadow: var(--glow-md);
  transform: translateY(-1px);
}
.btn-expand:active {
  transform: translateY(0);
}

.btn-delete {
  background: rgba(248,113,113,0.15);
  color: #f87171;
  border: 1px solid rgba(248,113,113,0.2);
}
.btn-delete:hover {
  background: rgba(248,113,113,0.25);
  box-shadow: 0 0 12px rgba(248,113,113,0.2), 0 0 24px rgba(248,113,113,0.08);
  transform: translateY(-1px);
}
.btn-delete:active {
  transform: translateY(0);
}
</style>
