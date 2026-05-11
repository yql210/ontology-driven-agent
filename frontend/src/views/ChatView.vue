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
      <h1>🔍 LayerKG Agent</h1>
      <p>代码知识图谱助手 — 问任何关于代码架构的问题</p>
    </header>
    <div ref="messagesContainer" class="messages">
      <div v-if="chatStore.messages.length === 0" class="empty-state">
        <p>👋 你好！我是 LayerKG 代码知识图谱助手</p>
        <p>可以帮你理解代码架构、查询依赖关系、搜索函数定义...</p>
      </div>
      <MessageBubble
        v-for="msg in chatStore.messages"
        :key="msg.id"
        :message="msg"
      />
    </div>
    <ChatInput :disabled="chatStore.isLoading" @send="handleSend" />
  </div>
</template>

<style scoped>
.chat-view {
  display: flex;
  flex-direction: column;
  height: 100vh;
  max-width: 900px;
  margin: 0 auto;
}
.chat-header {
  padding: 16px 20px;
  border-bottom: 1px solid #e0e0e0;
  background: white;
}
.chat-header h1 { margin: 0; font-size: 1.3em; }
.chat-header p { margin: 4px 0 0; color: #666; font-size: 0.9em; }
.messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px 20px;
  background: #fafafa;
}
.empty-state {
  text-align: center;
  color: #999;
  margin-top: 80px;
}
</style>
