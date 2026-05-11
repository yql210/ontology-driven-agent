<script setup lang="ts">
import type { Message } from '../api/types'
import MarkdownRenderer from './MarkdownRenderer.vue'
import ToolCallBlock from './ToolCallBlock.vue'

defineProps<{ message: Message; threadId?: string | null }>()
</script>

<template>
  <div class="message" :class="message.role">
    <div class="avatar">{{ message.role === 'user' ? '👤' : '🤖' }}</div>
    <div class="bubble">
      <MarkdownRenderer :content="message.content" />
      <div v-if="message.toolCalls?.length" class="tool-calls">
        <ToolCallBlock v-for="tc in message.toolCalls" :key="tc.id" :tool-call="tc" />
      </div>
      <span v-if="message.isStreaming" class="cursor">▊</span>
      <router-link v-if="threadId && !message.isStreaming && message.role === 'assistant'" :to="`/traces/${threadId}`" class="trace-link">
        📊 查看 Trace →
      </router-link>
    </div>
  </div>
</template>

<style scoped>
.message {
  display: flex;
  gap: 10px;
  margin: 12px 0;
  max-width: 800px;
}
.message.user { flex-direction: row-reverse; }
.avatar {
  font-size: 1.5em;
  flex-shrink: 0;
}
.bubble {
  padding: 10px 14px;
  border-radius: 12px;
  background: #f0f0f0;
  line-height: 1.6;
  min-width: 60px;
}
.message.user .bubble { background: #007bff; color: white; }
.message.error .bubble { background: #fff3cd; color: #856404; }
.cursor {
  animation: blink 0.7s infinite;
}
.trace-link {
  display: inline-block;
  margin-top: 8px;
  color: #3498db;
  text-decoration: none;
  font-size: 13px;
}
.trace-link:hover {
  text-decoration: underline;
}
@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}
</style>
