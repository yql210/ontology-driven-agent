<script setup lang="ts">
import { computed } from 'vue'
import { marked } from 'marked'
import hljs from 'highlight.js'
import 'highlight.js/styles/github-dark.css'
import type { Tokens } from 'marked'

const props = defineProps<{ content: string }>()

// 使用 marked.use 替代已废弃的 setOptions
marked.use({
  renderer: {
    code(token: Tokens.Code) {
      const validLang = token.lang && hljs.getLanguage(token.lang) ? token.lang : 'plaintext'
      const highlighted = hljs.highlight(token.text, { language: validLang }).value
      return `<pre><code class="hljs language-${validLang}">${highlighted}</code></pre>`
    },
  },
})

const rendered = computed(() => marked.parse(props.content || ''))
</script>

<template>
  <div class="markdown-body" v-html="rendered"></div>
</template>

<style scoped>
.markdown-body :deep(pre) {
  background: #1e1e2e;
  border-radius: 6px;
  padding: 12px;
  overflow-x: auto;
}
.markdown-body :deep(code) {
  font-family: 'Fira Code', monospace;
  font-size: 0.9em;
}
.markdown-body :deep(p) {
  margin: 0.5em 0;
}
</style>
