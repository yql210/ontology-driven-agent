import { createRouter, createWebHistory } from 'vue-router'
import ChatView from '../views/ChatView.vue'
import GraphView from '../views/GraphView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'chat', component: ChatView },
    { path: '/graph', name: 'graph', component: GraphView },
  ],
})

export default router
