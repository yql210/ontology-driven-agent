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
  padding: 12px 16px;
  background: white;
  border-top: 1px solid #e0e0e0;
}
input {
  flex: 1;
  padding: 10px 14px;
  border: 1px solid #ddd;
  border-radius: 8px;
  font-size: 14px;
  outline: none;
}
input:focus { border-color: #007bff; }
button {
  padding: 10px 20px;
  background: #007bff;
  color: white;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
}
button:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
