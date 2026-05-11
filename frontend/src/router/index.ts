import { createRouter, createWebHistory } from 'vue-router'
import ChatView from '../views/ChatView.vue'
import GraphView from '../views/GraphView.vue'
import TracesView from '../views/TracesView.vue'
import TraceDetailView from '../views/TraceDetailView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'chat', component: ChatView },
    { path: '/graph', name: 'graph', component: GraphView },
    { path: '/traces', name: 'traces', component: TracesView },
    { path: '/traces/:threadId', name: 'trace-detail', component: TraceDetailView },
  ],
})

export default router
