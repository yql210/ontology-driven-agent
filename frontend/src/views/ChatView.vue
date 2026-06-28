<script setup lang="ts">
import { useChatStore } from '../stores/chat'
import { ref } from 'vue'
import MessageBubble from '../components/MessageBubble.vue'
import ChatInput from '../components/ChatInput.vue'

const chatStore = useChatStore()
const messagesContainer = ref<HTMLElement>()

function handleSend(message: string) {
  chatStore.sendMessage(message)
  // 滚动到底部
  setTimeout(() => {
    messagesContainer.value?.scrollTo({ top: messagesContainer.value.scrollHeight, behavior: 'smooth' })
  }, 50)
}
</script>

<template>
  <div class="chat-view">
    <header class="chat-header">
      <h1>🔍 OntoAgent Agent</h1>
      <p>代码知识图谱助手 — 问任何关于代码架构的问题</p>
    </header>
    <div ref="messagesContainer" class="messages">
      <div v-if="chatStore.messages.length === 0" class="empty-state">
        <p>👋 你好！我是 OntoAgent 代码知识图谱助手</p>
        <p>可以帮你理解代码架构、查询依赖关系、搜索函数定义...</p>
      </div>
      <MessageBubble
        v-for="msg in chatStore.messages"
        :key="msg.id"
        :message="msg"
        :thread-id="chatStore.threadId"
        @approve="(id) => chatStore.handleApproval(id, true)"
        @reject="(id) => chatStore.handleApproval(id, false)"
      />
    </div>
    <ChatInput :disabled="chatStore.isLoading" @send="handleSend" />
  </div>
</template>

<style scoped>
.chat-view {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 56px);
  max-width: 900px;
  margin: 0 auto;
  animation: slide-up 0.3s ease-out;
}
.chat-header {
  padding: 20px 24px;
  background:
    linear-gradient(135deg, rgba(139,92,246,0.12), rgba(59,130,246,0.08), rgba(52,211,153,0.04)),
    linear-gradient(180deg, rgba(139,92,246,0.06), transparent);
  background-size: 200% 200%;
  animation: gradient-shift 8s ease infinite;
  border-bottom: 1px solid var(--border-dim);
  position: relative;
}
.chat-header h1 {
  margin: 0;
  font-size: 1.4em;
  font-weight: 600;
  background: linear-gradient(135deg, #a78bfa, #60a5fa);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}
.chat-header p {
  margin: 4px 0 0;
  color: var(--text-muted);
  font-size: 0.9em;
}
.messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px 24px;
  background: var(--bg-primary);
}
.empty-state {
  text-align: center;
  color: var(--text-muted);
  margin-top: 100px;
  animation: slide-up 0.4s ease-out;
}
.empty-state p:first-child {
  font-size: 48px;
  animation: float 3s ease-in-out infinite, glow-pulse 2s ease-in-out infinite;
}
.empty-state p:last-child {
  margin-top: 12px;
  font-size: 14px;
}
</style>
