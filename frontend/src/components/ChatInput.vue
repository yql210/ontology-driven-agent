<script setup lang="ts">
import { ref } from 'vue'

const emit = defineEmits<{ send: [message: string] }>()
defineProps<{ disabled: boolean }>()
const input = ref('')

function handleSubmit() {
  const msg = input.value.trim()
  if (!msg) return
  emit('send', msg)
  input.value = ''
}
</script>

<template>
  <div class="chat-input">
    <input
      v-model="input"
      placeholder="输入问题，如：ConceptAligner 在哪个文件？"
      :disabled="disabled"
      @keydown.enter="handleSubmit"
    />
    <button :disabled="disabled || !input.trim()" @click="handleSubmit">发送</button>
  </div>
</template>

<style scoped>
.chat-input {
  display: flex;
  gap: 8px;
  padding: 16px 24px;
  background: var(--bg-secondary);
  border-top: 1px solid var(--border-dim);
}
input {
  flex: 1;
  padding: 12px 16px;
  background: var(--bg-tertiary);
  color: var(--text-primary);
  border: 1px solid var(--border-dim);
  border-radius: var(--radius-md);
  font-size: 14px;
  font-family: var(--font-sans);
  outline: none;
  transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
}
input::placeholder { color: var(--text-muted); }
input:focus {
  border-color: rgba(139,92,246,0.5);
  box-shadow: 0 0 0 3px rgba(139,92,246,0.1);
}
button {
  padding: 10px 20px;
  background: linear-gradient(135deg, #8b5cf6, #3b82f6);
  color: #fff;
  border: none;
  border-radius: var(--radius-md);
  cursor: pointer;
  font-size: 14px;
  font-weight: 500;
  font-family: var(--font-sans);
  transition: background var(--transition-fast), transform var(--transition-fast), box-shadow var(--transition-fast);
}
button:hover {
  background: linear-gradient(135deg, #7c3aed, #2563eb);
  transform: translateY(-1px);
  box-shadow: 0 0 12px rgba(139,92,246,0.25), 0 0 24px rgba(139,92,246,0.1);
}
button:disabled { opacity: 0.5; cursor: not-allowed; transform: none; box-shadow: none; }
</style>
