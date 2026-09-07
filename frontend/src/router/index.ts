import { createRouter, createWebHistory } from 'vue-router'
import ChatView from '../views/ChatView.vue'
import GraphView from '../views/GraphView.vue'
import RepoView from '../views/RepoView.vue'
import TracesView from '../views/TracesView.vue'
import TraceDetailView from '../views/TraceDetailView.vue'
import WorkspaceOverviewView from '../views/WorkspaceOverviewView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'chat', component: ChatView },
    { path: '/graph', name: 'graph', component: GraphView },
    { path: '/repos', name: 'repos', component: RepoView },
    { path: '/traces', name: 'traces', component: TracesView },
    { path: '/traces/:threadId', name: 'trace-detail', component: TraceDetailView },
    { path: '/workspaces/:workspace_id', name: 'workspace-overview', component: WorkspaceOverviewView },
  ],
})

export default router
