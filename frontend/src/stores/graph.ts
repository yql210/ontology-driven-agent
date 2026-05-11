import { defineStore } from 'pinia'
import type { GraphData, GraphStats, NodeDetail } from '../api/types'
import { fetchGraph, fetchGraphStats, fetchNodeDetail, deleteNode } from '../api/graph'

interface GraphState {
  graphData: GraphData
  stats: GraphStats | null
  selectedNode: NodeDetail | null
  isLoading: boolean
  error: string | null
}

export const useGraphStore = defineStore('graph', {
  state: (): GraphState => ({
    graphData: { nodes: [], edges: [] },
    stats: null,
    selectedNode: null,
    isLoading: false,
    error: null,
  }),

  actions: {
    async loadGraph(params?: { center?: string; depth?: number; limit?: number; type?: string }) {
      this.isLoading = true
      this.error = null
      try {
        this.graphData = await fetchGraph(params)
      } catch (e) {
        this.error = e instanceof Error ? e.message : 'Failed to load graph'
        throw e
      } finally {
        this.isLoading = false
      }
    },

    async loadStats() {
      try {
        this.stats = await fetchGraphStats()
      } catch (e) {
        console.error('Failed to load stats:', e)
      }
    },

    async selectNode(nodeId: string) {
      this.isLoading = true
      this.error = null
      try {
        this.selectedNode = await fetchNodeDetail(nodeId)
      } catch (e) {
        this.error = e instanceof Error ? e.message : 'Failed to load node detail'
        throw e
      } finally {
        this.isLoading = false
      }
    },

    async expandNode(name: string) {
      this.isLoading = true
      this.error = null
      try {
        const newData = await fetchGraph({ center: name, depth: 1, limit: 50 })
        // 合并新节点和边
        const existingIds = new Set(this.graphData.nodes.map((n) => n.id))
        for (const node of newData.nodes) {
          if (!existingIds.has(node.id)) {
            this.graphData.nodes.push(node)
          }
        }
        const existingEdges = new Set(this.graphData.edges.map((e) => `${e.source}-${e.target}-${e.type}`))
        for (const edge of newData.edges) {
          const key = `${edge.source}-${edge.target}-${edge.type}`
          if (!existingEdges.has(key)) {
            this.graphData.edges.push(edge)
          }
        }
      } catch (e) {
        this.error = e instanceof Error ? e.message : 'Failed to expand node'
        throw e
      } finally {
        this.isLoading = false
      }
    },

    async deleteNode(nodeId: string) {
      this.isLoading = true
      this.error = null
      try {
        await deleteNode(nodeId)
        // 从本地数据中移除
        this.graphData.nodes = this.graphData.nodes.filter((n) => n.id !== nodeId)
        this.graphData.edges = this.graphData.edges.filter((e) => e.source !== nodeId && e.target !== nodeId)
        if (this.selectedNode?.id === nodeId) {
          this.selectedNode = null
        }
      } catch (e) {
        this.error = e instanceof Error ? e.message : 'Failed to delete node'
        throw e
      } finally {
        this.isLoading = false
      }
    },

    async refresh() {
      await this.loadGraph()
      await this.loadStats()
    },
  },
})
