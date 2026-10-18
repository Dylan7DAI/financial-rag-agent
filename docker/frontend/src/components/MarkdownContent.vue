<script setup>
import { computed } from 'vue'
import DOMPurify from 'dompurify'
import { markdown } from '../lib/markdown.js'
const props = defineProps({ content: { type: String, default: '' }, citationIds: { type: Array, default: () => [] } })
const emit = defineEmits(['citation'])
const html = computed(() => DOMPurify.sanitize(markdown.render(props.content, { citationIds: props.citationIds }), {
  ALLOWED_TAGS: ['button', 'p', 'br', 'strong', 'em', 's', 'a', 'ul', 'ol', 'li', 'blockquote', 'pre', 'code', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr', 'table', 'thead', 'tbody', 'tr', 'th', 'td'],
  ALLOWED_ATTR: ['href', 'title', 'start', 'align', 'type', 'class', 'aria-label', 'data-citation-id'], ALLOW_DATA_ATTR: false,
}))
function click(event) {
  const id = Number(event.target.closest('[data-citation-id]')?.dataset.citationId)
  if (props.citationIds.includes(id)) emit('citation', id)
}
</script>
<template><div class="markdown-content" @click="click" v-html="html" /></template>
