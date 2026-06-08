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
      <template v-if="message.blocks?.length">
        <template v-for="(block, idx) in message.blocks" :key="idx">
          <MarkdownRenderer v-if="block.type === 'text' && block.content" :content="block.content" />
          <ToolCallBlock v-else-if="block.type === 'tool_call'" :tool-call="block.toolCall" />
        </template>
      </template>
      <MarkdownRenderer v-else :content="message.content" />
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
  margin-bottom: 16px;
  max-width: 70%;
  animation: slide-up 0.3s ease-out;
}
.message.user { flex-direction: row-reverse; }
.avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 600;
}
.message.user .avatar {
  background: linear-gradient(135deg, #8b5cf6, #3b82f6);
  color: #fff;
}
.message:not(.user) .avatar {
  background: var(--bg-tertiary);
  border: 1px solid var(--border-default);
  color: var(--primary-light);
  font-size: 1.2em;
}
.bubble {
  padding: 12px 16px;
  border-radius: 16px 16px 16px 4px;
  background: var(--bg-glass);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid var(--border-default);
  color: var(--text-primary);
  line-height: 1.6;
  min-width: 60px;
  font-size: 14px;
  overflow-wrap: break-word;
  word-break: break-word;
}
.message.user .bubble {
  background: linear-gradient(135deg, #8b5cf6, #3b82f6);
  border: none;
  color: #fff;
  border-radius: 16px 16px 4px 16px;
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.1), inset 0 -1px 0 rgba(0,0,0,0.1);
}
.message:not(.user):not(.error) .bubble {
  border-image: linear-gradient(135deg, rgba(139,92,246,0.3), rgba(59,130,246,0.2), rgba(139,92,246,0.15)) 1;
  border-image-slice: 1;
}
.message.error .bubble {
  background: rgba(248,113,113,0.15);
  border-color: rgba(248,113,113,0.3);
  color: #f87171;
}
.cursor {
  animation: blink 0.7s infinite;
  color: var(--primary-light);
}
.trace-link {
  display: inline-block;
  margin-top: 8px;
  color: var(--primary-light);
  text-decoration: none;
  font-size: 13px;
  transition: color var(--transition-fast), text-shadow var(--transition-fast);
}
.trace-link:hover {
  text-decoration: underline;
  color: #fff;
  text-shadow: 0 0 8px rgba(139,92,246,0.4);
}
@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}
</style>
