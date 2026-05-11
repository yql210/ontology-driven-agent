<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import cytoscape, { type Core, type ElementDefinition } from 'cytoscape'
import { useGraphStore } from '../stores/graph'
import type { GraphNode } from '../api/types'
import NodeDetail from '../components/NodeDetail.vue'

const graphStore = useGraphStore()
const containerRef = ref<HTMLElement>()
const searchQuery = ref('')
const selectedType = ref('')
let cy: Core | null = null

const typeColors: Record<string, string> = {
  CodeEntity: '#4caf50',
  ConceptEntity: '#2196f3',
  DocEntity: '#ff9800',
  ResourceEntity: '#9c27b0',
  ModuleEntity: '#00bcd4',
  ChangeSetEntity: '#f44336',
}

const labelMap: Record<string, string> = {
  CodeEntity: '代码',
  ConceptEntity: '概念',
  DocEntity: '文档',
  ResourceEntity: '资源',
  ModuleEntity: '模块',
  ChangeSetEntity: '变更',
}

// 前端过滤节点
const filteredNodes = computed<GraphNode[]>(() => {
  let nodes = graphStore.graphData.nodes
  if (searchQuery.value) {
    nodes = nodes.filter((n) => n.name.toLowerCase().includes(searchQuery.value.toLowerCase()))
  }
  if (selectedType.value) {
    nodes = nodes.filter((n) => n.neo4jLabel === selectedType.value)
  }
  return nodes
})

onMounted(async () => {
  await graphStore.loadStats()
  await graphStore.loadGraph()
  initCytoscape()
  updateGraph()
})

onUnmounted(() => {
  cy?.destroy()
})

watch([() => graphStore.graphData, filteredNodes], () => updateGraph(), { deep: true })

function initCytoscape() {
  if (!containerRef.value) return
  cy = cytoscape({
    container: containerRef.value,
    style: [
      {
        selector: 'node',
        style: {
          'background-color': 'data(color)',
          label: 'data(label)',
          'text-valign': 'center',
          'text-halign': 'center',
          width: '30px',
          height: '30px',
          'font-size': '10px',
        },
      },
      {
        selector: 'edge',
        style: {
          width: 2,
          'line-color': '#999',
          'target-arrow-color': '#999',
          'target-arrow-shape': 'triangle',
          'curve-style': 'bezier',
          'font-size': '8px',
          'text-rotation': 'autorotate',
          'text-margin-y': '-5px',
        } as any,
      },
      {
        selector: 'node:selected',
        style: {
          'border-width': 3,
          'border-color': '#000',
        },
      },
    ],
    layout: {
      name: 'cose',
      animate: false,
    },
    minZoom: 0.2,
    maxZoom: 3,
  })

  cy.on('tap', 'node', (evt) => {
    const node = evt.target
    const nodeId = node.id()
    graphStore.selectNode(nodeId)
  })

  cy.on('dbltap', 'node', (evt) => {
    const node = evt.target
    const nodeName = node.data('name') as string
    graphStore.expandNode(nodeName)
  })
}

function updateGraph() {
  if (!cy) return

  const nodeIds = new Set(filteredNodes.value.map((n) => n.id))
  const nodes: ElementDefinition[] = filteredNodes.value.map((n) => ({
    data: {
      id: n.id,
      label: n.name,
      name: n.name,
      color: typeColors[n.neo4jLabel] || '#999',
    },
  }))
  // 只保留两端都在过滤后节点集合中的边
  const edges: ElementDefinition[] = graphStore.graphData.edges
    .filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target))
    .map((e) => ({
      data: {
        source: e.source,
        target: e.target,
        label: String(e.type),
      },
    }))

  cy.elements().remove()
  cy.add([...nodes, ...edges])
  cy.layout({ name: 'cose', animate: false }).run()
}

function handleExpand(name: string) {
  graphStore.expandNode(name)
}

function handleDelete(id: string) {
  graphStore.deleteNode(id)
}

async function handleRefresh() {
  await graphStore.refresh()
  updateGraph()
}

function handleReset() {
  searchQuery.value = ''
  selectedType.value = ''
}
</script>

<template>
  <div class="graph-view">
    <div class="graph-header">
      <div class="header-left">
        <input v-model="searchQuery" type="text" placeholder="搜索节点..." class="search-input" />
        <select v-model="selectedType" class="type-select">
          <option value="">全部类型</option>
          <option v-for="[key, label] of Object.entries(labelMap)" :key="key" :value="key">
            {{ label }}
          </option>
        </select>
        <button class="btn-reset" @click="handleReset">重置</button>
      </div>
      <div class="header-right">
        <button class="btn-refresh" @click="handleRefresh">刷新</button>
      </div>
    </div>

    <div class="graph-main">
      <div class="graph-canvas-container">
        <div ref="containerRef" class="graph-canvas"></div>
        <div class="graph-legend">
          <div v-for="[key, label] of Object.entries(labelMap)" :key="key" class="legend-item">
            <span class="legend-color" :style="{ background: typeColors[key] }"></span>
            <span class="legend-label">{{ label }}</span>
          </div>
        </div>
      </div>
      <NodeDetail :node="graphStore.selectedNode" @expand="handleExpand" @delete="handleDelete" />
    </div>

    <div class="graph-footer">
      <span v-if="graphStore.stats">
        节点: {{ graphStore.stats.node_count }} | 边: {{ graphStore.stats.edge_count }}
      </span>
      <span v-if="graphStore.isLoading">加载中...</span>
      <span v-if="graphStore.error" class="error">{{ graphStore.error }}</span>
    </div>
  </div>
</template>

<style scoped>
.graph-view {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 50px);
}

.graph-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  background: #f9f9f9;
  border-bottom: 1px solid #ddd;
}

.header-left {
  display: flex;
  gap: 8px;
  align-items: center;
}

.search-input {
  padding: 6px 12px;
  border: 1px solid #ddd;
  border-radius: 4px;
  font-size: 14px;
  width: 200px;
}

.type-select {
  padding: 6px 12px;
  border: 1px solid #ddd;
  border-radius: 4px;
  font-size: 14px;
}

.btn-reset,
.btn-refresh {
  padding: 6px 12px;
  border: 1px solid #ddd;
  border-radius: 4px;
  background: #fff;
  cursor: pointer;
  font-size: 14px;
}

.btn-reset:hover,
.btn-refresh:hover {
  background: #f0f0f0;
}

.graph-main {
  flex: 1;
  display: flex;
  overflow: hidden;
}

.graph-canvas-container {
  flex: 1;
  position: relative;
}

.graph-canvas {
  width: 100%;
  height: 100%;
  background: #fafafa;
}

.graph-legend {
  position: absolute;
  bottom: 16px;
  left: 16px;
  background: rgba(255, 255, 255, 0.9);
  padding: 12px;
  border-radius: 4px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.legend-item {
  display: flex;
  align-items: center;
  margin-bottom: 4px;
}

.legend-item:last-child {
  margin-bottom: 0;
}

.legend-color {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  margin-right: 8px;
}

.legend-label {
  font-size: 12px;
  color: #555;
}

.graph-footer {
  padding: 8px 16px;
  background: #f9f9f9;
  border-top: 1px solid #ddd;
  font-size: 13px;
  color: #666;
  display: flex;
  gap: 16px;
}

.error {
  color: #f44336;
}
</style>
