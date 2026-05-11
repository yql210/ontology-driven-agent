<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import cytoscape, { type Core, type ElementDefinition } from 'cytoscape'
import { useGraphStore } from '../stores/graph'
import type { GraphNode } from '../api/types'
import NodeDetail from '../components/NodeDetail.vue'

const graphStore = useGraphStore()
const containerRef = ref<HTMLElement>()
const searchQuery = ref('')
const selectedTypes = ref<string[]>([])
const typeDropdownOpen = ref(false)
let cy: Core | null = null

const labelMap: Record<string, string> = {
  CodeEntity: '代码',
  ConceptEntity: '概念',
  DocEntity: '文档',
  ResourceEntity: '资源',
  ModuleEntity: '模块',
  ChangeSetEntity: '变更',
}

const entityTypeLabels = Object.keys(labelMap) as string[]

// 前端过滤节点（仅按类型筛选，搜索改为高亮）
const filteredNodes = computed<GraphNode[]>(() => {
  let nodes = graphStore.graphData.nodes
  if (selectedTypes.value.length > 0) {
    nodes = nodes.filter((n) => selectedTypes.value.includes(n.neo4jLabel))
  }
  return nodes
})

// 当前显示的节点数（用于统计栏）
const displayedCount = computed(() => filteredNodes.value.length)

onMounted(async () => {
  await graphStore.loadStats()
  await graphStore.loadGraph()
  initCytoscape()
  updateGraph()
  setupClickOutside()
})

onUnmounted(() => {
  cy?.destroy()
})

watch([() => graphStore.graphData, filteredNodes], () => updateGraph(), { deep: true })

// Fix 2: 搜索高亮（替代过滤）
watch(searchQuery, (query) => {
  if (!cy) return
  cy.elements().unselect()
  if (!query) return
  const match = cy.nodes().filter((n) => {
    const name = n.data('name') as string
    return name?.toLowerCase().includes(query.toLowerCase())
  })
  if (match.length > 0) {
    const first = match[0]
    first.select()
    cy.animate({
      center: { eles: first },
      duration: 300,
    })
  }
})

function initCytoscape() {
  if (!containerRef.value) return
  cy = cytoscape({
    container: containerRef.value,
    style: [
      // Fix 1: 默认灰色，用 CSS selector 按类型着色
      {
        selector: 'node',
        style: {
          'background-color': '#999',
          label: 'data(label)',
          'text-valign': 'center',
          'text-halign': 'center',
          width: '30px',
          height: '30px',
          'font-size': '10px',
        },
      },
      // Fix 1: 按类型着色（颜色映射：CodeEntity=蓝, ConceptEntity=绿, ModuleEntity=橙, DocEntity=紫, ResourceEntity=粉, ChangeSetEntity=红）
      {
        selector: 'node[neo4jLabel="CodeEntity"]',
        style: { 'background-color': '#4A90D9' },
      },
      {
        selector: 'node[neo4jLabel="ConceptEntity"]',
        style: { 'background-color': '#27AE60' },
      },
      {
        selector: 'node[neo4jLabel="ModuleEntity"]',
        style: { 'background-color': '#F39C12' },
      },
      {
        selector: 'node[neo4jLabel="DocEntity"]',
        style: { 'background-color': '#8E44AD' },
      },
      {
        selector: 'node[neo4jLabel="ResourceEntity"]',
        style: { 'background-color': '#E91E8C' },
      },
      {
        selector: 'node[neo4jLabel="ChangeSetEntity"]',
        style: { 'background-color': '#E74C3C' },
      },
      // Fix 4: 边标签显示
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
          label: 'data(label)',
        } as any,
      },
      // Fix 4: 选中高亮用金色
      {
        selector: 'node:selected',
        style: {
          'border-width': 3,
          'border-color': '#FFD700',
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
      neo4jLabel: n.neo4jLabel,
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

// Fix 3: 多选下拉面板的点击外部关闭逻辑
function setupClickOutside() {
  const handler = (e: MouseEvent) => {
    const target = e.target as HTMLElement
    if (!target.closest('.type-dropdown')) {
      typeDropdownOpen.value = false
    }
  }
  document.addEventListener('click', handler)
  onUnmounted(() => document.removeEventListener('click', handler))
}

function toggleTypeDropdown() {
  typeDropdownOpen.value = !typeDropdownOpen.value
}

function toggleType(type: string) {
  const idx = selectedTypes.value.indexOf(type)
  if (idx === -1) {
    selectedTypes.value.push(type)
  } else {
    selectedTypes.value.splice(idx, 1)
  }
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
  selectedTypes.value = []
}
</script>

<template>
  <div class="graph-view">
    <div class="graph-header">
      <div class="header-left">
        <input v-model="searchQuery" type="text" placeholder="搜索节点..." class="search-input" />
        <div class="type-dropdown">
          <button class="type-select-btn" @click="toggleTypeDropdown">
            类型筛选 ({{ selectedTypes.length }})
            <span class="dropdown-arrow">{{ typeDropdownOpen ? '▲' : '▼' }}</span>
          </button>
          <div v-if="typeDropdownOpen" class="type-dropdown-panel">
            <label v-for="type in entityTypeLabels" :key="type" class="type-checkbox-item">
              <input type="checkbox" :value="type" :checked="selectedTypes.includes(type)" @change="toggleType(type)" />
              {{ labelMap[type] }}
            </label>
          </div>
        </div>
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
          <div class="legend-item">
            <span class="legend-color" style="background: #4A90D9"></span>
            <span class="legend-label">{{ labelMap.CodeEntity }}</span>
          </div>
          <div class="legend-item">
            <span class="legend-color" style="background: #27AE60"></span>
            <span class="legend-label">{{ labelMap.ConceptEntity }}</span>
          </div>
          <div class="legend-item">
            <span class="legend-color" style="background: #F39C12"></span>
            <span class="legend-label">{{ labelMap.ModuleEntity }}</span>
          </div>
          <div class="legend-item">
            <span class="legend-color" style="background: #8E44AD"></span>
            <span class="legend-label">{{ labelMap.DocEntity }}</span>
          </div>
          <div class="legend-item">
            <span class="legend-color" style="background: #E91E8C"></span>
            <span class="legend-label">{{ labelMap.ResourceEntity }}</span>
          </div>
          <div class="legend-item">
            <span class="legend-color" style="background: #E74C3C"></span>
            <span class="legend-label">{{ labelMap.ChangeSetEntity }}</span>
          </div>
        </div>
      </div>
      <NodeDetail :node="graphStore.selectedNode" @expand="handleExpand" @delete="handleDelete" />
    </div>

    <div class="graph-footer">
      <span v-if="graphStore.stats">
        节点: {{ graphStore.stats.node_count }} | 边: {{ graphStore.stats.edge_count }} | 当前显示: {{ displayedCount }}
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

.type-dropdown {
  position: relative;
}

.type-select-btn {
  padding: 6px 12px;
  border: 1px solid #ddd;
  border-radius: 4px;
  background: #fff;
  cursor: pointer;
  font-size: 14px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.type-select-btn:hover {
  background: #f0f0f0;
}

.dropdown-arrow {
  font-size: 10px;
}

.type-dropdown-panel {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  background: #fff;
  border: 1px solid #ddd;
  border-radius: 4px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  z-index: 100;
  min-width: 120px;
}

.type-checkbox-item {
  display: flex;
  align-items: center;
  padding: 8px 12px;
  cursor: pointer;
  white-space: nowrap;
}

.type-checkbox-item:hover {
  background: #f5f5f5;
}

.type-checkbox-item input {
  margin-right: 8px;
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
