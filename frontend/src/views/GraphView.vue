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
          'background-color': '#64748b',
          label: 'data(label)',
          'text-valign': 'center',
          'text-halign': 'center',
          width: '45px',
          height: '45px',
          'border-width': 2,
          'border-color': '#64748b',
          'font-size': '11px',
          'text-outline-color': '#0f172a',
          'text-outline-width': '2px',
          color: '#e2e8f0',
        },
      },
      // Neon colors by entity type
      {
        selector: 'node[neo4jLabel="CodeEntity"]',
        style: { 'background-color': '#60a5fa', 'border-color': '#93c5fd' },
      },
      {
        selector: 'node[neo4jLabel="ConceptEntity"]',
        style: { 'background-color': '#34d399', 'border-color': '#6ee7b7' },
      },
      {
        selector: 'node[neo4jLabel="ModuleEntity"]',
        style: { 'background-color': '#fbbf24', 'border-color': '#fcd34d' },
      },
      {
        selector: 'node[neo4jLabel="DocEntity"]',
        style: { 'background-color': '#a78bfa', 'border-color': '#c4b5fd' },
      },
      {
        selector: 'node[neo4jLabel="ResourceEntity"]',
        style: { 'background-color': '#f472b6', 'border-color': '#f9a8d4' },
      },
      {
        selector: 'node[neo4jLabel="ChangeSetEntity"]',
        style: { 'background-color': '#f87171', 'border-color': '#fca5a5' },
      },
      // Fix 4: 边标签显示
      {
        selector: 'edge',
        style: {
          width: 1.5,
          'line-color': 'rgba(148,163,184,0.25)',
          'target-arrow-color': 'rgba(148,163,184,0.25)',
          'target-arrow-shape': 'triangle',
          'curve-style': 'bezier',
          'font-size': '9px',
          color: '#64748b',
          'text-rotation': 'autorotate',
          'text-margin-y': '-5px',
          label: 'data(label)',
        } as any,
      },
      // Selected node glow
      {
        selector: 'node:selected',
        style: {
          'border-width': 3,
          'border-color': '#fbbf24',
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
            <span class="legend-color" style="background: #60a5fa"></span>
            <span class="legend-label">{{ labelMap.CodeEntity }}</span>
          </div>
          <div class="legend-item">
            <span class="legend-color" style="background: #34d399"></span>
            <span class="legend-label">{{ labelMap.ConceptEntity }}</span>
          </div>
          <div class="legend-item">
            <span class="legend-color" style="background: #fbbf24"></span>
            <span class="legend-label">{{ labelMap.ModuleEntity }}</span>
          </div>
          <div class="legend-item">
            <span class="legend-color" style="background: #a78bfa"></span>
            <span class="legend-label">{{ labelMap.DocEntity }}</span>
          </div>
          <div class="legend-item">
            <span class="legend-color" style="background: #f472b6"></span>
            <span class="legend-label">{{ labelMap.ResourceEntity }}</span>
          </div>
          <div class="legend-item">
            <span class="legend-color" style="background: #f87171"></span>
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
  height: calc(100vh - 56px);
  animation: slide-up 0.3s ease-out;
}

.graph-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  background: var(--bg-glass);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--border-dim);
}

.header-left {
  display: flex;
  gap: 8px;
  align-items: center;
}

.search-input {
  padding: 8px 16px;
  background: var(--bg-tertiary);
  color: var(--text-primary);
  border: 1px solid var(--border-dim);
  border-radius: var(--radius-pill);
  font-size: 14px;
  width: 200px;
  font-family: var(--font-sans);
  outline: none;
  transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
}
.search-input::placeholder { color: var(--text-muted); }
.search-input:focus {
  border-color: transparent;
  background-image: linear-gradient(var(--bg-tertiary), var(--bg-tertiary)), linear-gradient(135deg, #8b5cf6, #3b82f6);
  background-origin: border-box;
  background-clip: padding-box, border-box;
  box-shadow: 0 0 12px rgba(139,92,246,0.15);
}

.type-dropdown {
  position: relative;
}

.type-select-btn {
  padding: 8px 16px;
  background: var(--bg-tertiary);
  color: var(--text-secondary);
  border: 1px solid var(--border-dim);
  border-radius: var(--radius-pill);
  cursor: pointer;
  font-size: 14px;
  font-family: var(--font-sans);
  display: flex;
  align-items: center;
  gap: 6px;
  transition: color var(--transition-fast), border-color var(--transition-fast);
}
.type-select-btn:hover {
  color: var(--text-primary);
  border-color: var(--border-default);
}

.dropdown-arrow {
  font-size: 10px;
}

.type-dropdown-panel {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  background: rgba(30, 41, 59, 0.95);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  box-shadow: 0 8px 32px rgba(0,0,0,0.4);
  z-index: 100;
  min-width: 140px;
  padding: 4px;
}

.type-checkbox-item {
  display: flex;
  align-items: center;
  padding: 8px 12px;
  cursor: pointer;
  white-space: nowrap;
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  font-size: 14px;
  transition: background var(--transition-fast);
}
.type-checkbox-item:hover {
  background: rgba(148,163,184,0.05);
}
.type-checkbox-item input {
  margin-right: 8px;
  accent-color: var(--primary);
}

.btn-reset,
.btn-refresh {
  padding: 8px 16px;
  background: var(--bg-tertiary);
  color: var(--text-secondary);
  border: 1px solid var(--border-dim);
  border-radius: var(--radius-pill);
  cursor: pointer;
  font-size: 14px;
  font-family: var(--font-sans);
  transition: color var(--transition-fast), border-color var(--transition-fast), background var(--transition-fast);
}
.btn-reset:hover,
.btn-refresh:hover {
  color: var(--text-primary);
  border-color: var(--border-default);
  background: rgba(51, 65, 85, 0.8);
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
  background: var(--bg-primary);
}

.graph-legend {
  position: absolute;
  bottom: 16px;
  left: 16px;
  background: rgba(30, 41, 59, 0.7);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  padding: 14px;
  box-shadow: var(--glow-xs);
}

.legend-item {
  display: flex;
  align-items: center;
  margin-bottom: 6px;
}
.legend-item:last-child {
  margin-bottom: 0;
}

.legend-color {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  margin-right: 10px;
  box-shadow: 0 0 6px currentColor;
}

.legend-label {
  font-size: 12px;
  color: var(--text-secondary);
}

.graph-footer {
  padding: 10px 16px;
  background: var(--bg-glass);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-top: 1px solid var(--border-dim);
  font-size: 12px;
  color: var(--text-muted);
  display: flex;
  gap: 16px;
}

.graph-footer span {
  background: linear-gradient(135deg, #a78bfa, #60a5fa);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  font-weight: 600;
}

.error {
  color: #f87171;
  -webkit-text-fill-color: #f87171;
}
</style>
