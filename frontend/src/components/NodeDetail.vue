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
  background: #f5f5f5;
  border-left: 1px solid #ddd;
  display: flex;
  flex-direction: column;
}

.empty-state {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #999;
}

.detail-content {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
}

.detail-header {
  margin-bottom: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid #ddd;
}

.detail-header h3 {
  margin: 0 0 8px 0;
  font-size: 18px;
}

.node-type {
  display: inline-block;
  padding: 2px 8px;
  background: #e3f2fd;
  color: #1976d2;
  border-radius: 4px;
  font-size: 12px;
}

.detail-section {
  margin-bottom: 16px;
}

.detail-section h4 {
  margin: 0 0 8px 0;
  font-size: 14px;
  color: #555;
}

.empty-hint {
  color: #999;
  font-size: 13px;
  font-style: italic;
}

.property-list,
.relation-list {
  background: #fff;
  border-radius: 4px;
  padding: 8px;
}

.property-item {
  display: flex;
  justify-content: space-between;
  padding: 4px 0;
  border-bottom: 1px solid #f0f0f0;
}

.property-item:last-child {
  border-bottom: none;
}

.prop-key {
  color: #666;
  font-size: 13px;
}

.prop-value {
  color: #333;
  font-size: 13px;
  word-break: break-all;
}

.relation-item {
  padding: 4px 0;
  font-size: 13px;
}

.rel-source,
.rel-target {
  color: #333;
}

.rel-type {
  color: #666;
  margin: 0 4px;
}

.detail-actions {
  display: flex;
  gap: 8px;
  padding-top: 16px;
  border-top: 1px solid #ddd;
}

.btn-expand,
.btn-delete {
  flex: 1;
  padding: 8px;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  font-size: 14px;
}

.btn-expand {
  background: #2196f3;
  color: white;
}

.btn-expand:hover {
  background: #1976d2;
}

.btn-delete {
  background: #f44336;
  color: white;
}

.btn-delete:hover {
  background: #d32f2f;
}
</style>
