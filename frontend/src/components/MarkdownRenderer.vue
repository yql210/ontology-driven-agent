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
  background: var(--bg-primary);
  border-radius: var(--radius-sm);
  padding: 16px;
  overflow-x: auto;
  border: 1px solid var(--border-dim);
}
.markdown-body :deep(code) {
  font-family: var(--font-mono);
  font-size: 13px;
}
.markdown-body :deep(:not(pre) > code) {
  background: var(--bg-tertiary);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 0.9em;
  color: var(--primary-light);
}
.markdown-body :deep(p) {
  margin: 0.5em 0;
}
.markdown-body :deep(a) {
  color: var(--primary-light);
  transition: color var(--transition-fast);
}
.markdown-body :deep(a:hover) {
  text-decoration: underline;
}
.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3),
.markdown-body :deep(h4) {
  color: var(--text-primary);
  font-weight: 600;
}
.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  padding-left: 20px;
}
</style>
