<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import MarkdownContent from './MarkdownContent.vue'
defineProps({ message: Object })
const emit = defineEmits(['close'])
const panel = ref(null)
let previousFocus
function keydown(event) {
  if (event.key === 'Escape') emit('close')
  if (event.key !== 'Tab' || !window.matchMedia('(max-width: 1100px)').matches) return
  const buttons = [...panel.value.querySelectorAll('button, summary, a[href]')].filter(el => el.getClientRects().length)
  const first = buttons[0], last = buttons.at(-1)
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
  if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
}
onMounted(() => { previousFocus = document.activeElement; panel.value.querySelector('button').focus() })
onUnmounted(() => previousFocus?.focus())
</script>
<template>
  <div class="drawer-backdrop evidence-backdrop" @click="$emit('close')" />
  <aside ref="panel" class="evidence-panel" aria-label="检索命中片段" @keydown="keydown">
    <header class="evidence-header"><div><span class="eyebrow">本轮检索</span><h2>检索命中片段 <span class="count-badge">{{ message.evidences.length }}</span></h2></div><button class="icon-button" aria-label="关闭检索片段" @click="$emit('close')"><AppIcon name="close" /></button></header>
    <div class="evidence-scroll">
      <p class="evidence-notice">这些是检索候选片段，尚未与回答所列来源逐一绑定。文件或页码可能不同，请分别核验。</p>
      <p class="evidence-question">{{ message.query }}</p>
      <p v-if="!message.evidences.length" class="muted">本条回答尚未收到检索片段。无片段不代表已证明没有相关资料。</p>
      <article v-for="(evidence, index) in message.evidences" :key="index" class="evidence-card">
        <div class="evidence-card-heading"><AppIcon name="file" /><h3>{{ evidence.pdf }}</h3></div>
        <div class="evidence-meta"><span>后端页码：{{ evidence.page }}</span><span>匹配度未校准</span></div>
        <MarkdownContent :content="evidence.content" />
        <details><summary>查看片段原文</summary><pre class="raw-text">{{ evidence.content }}</pre></details>
        <details class="debug-details"><summary>调试详情</summary><p>原始分数：{{ evidence.score ?? '缺失' }}。分数不代表正确率，0 也可能由缺失值转换而来。</p></details>
      </article>
    </div>
  </aside>
</template>
