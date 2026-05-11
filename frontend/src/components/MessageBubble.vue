<script setup lang="ts">
import type { Message } from '../api/types'
import MarkdownRenderer from './MarkdownRenderer.vue'
import ToolCallBlock from './ToolCallBlock.vue'

defineProps<{ message: Message }>()
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
@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}
</style>
