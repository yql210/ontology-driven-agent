import { createRouter, createWebHistory } from 'vue-router'
import ChatView from '../views/ChatView.vue'
import GraphView from '../views/GraphView.vue'
import RepoView from '../views/RepoView.vue'
import TracesView from '../views/TracesView.vue'
import TraceDetailView from '../views/TraceDetailView.vue'
import WorkspaceOverviewView from '../views/WorkspaceOverviewView.vue'
import ServiceDirectoryView from '../views/ServiceDirectoryView.vue'
import DependencyTopologyView from '../views/DependencyTopologyView.vue'
import ChangeQualityView from '../views/ChangeQualityView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'chat', component: ChatView },
    { path: '/graph', name: 'graph', component: GraphView },
    { path: '/repos', name: 'repos', component: RepoView },
    { path: '/traces', name: 'traces', component: TracesView },
    { path: '/traces/:threadId', name: 'trace-detail', component: TraceDetailView },
    { path: '/workspaces/:workspace_id', name: 'workspace-overview', component: WorkspaceOverviewView },
    { path: '/workspaces/:workspace_id/services', name: 'workspace-services', component: ServiceDirectoryView },
    { path: '/workspaces/:workspace_id/topology', name: 'workspace-topology', component: DependencyTopologyView },
    { path: '/workspaces/:workspace_id/quality', name: 'workspace-quality', component: ChangeQualityView },
  ],
})

export default router
