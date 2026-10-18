<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import type { Citation, Message } from '../lib/contracts'
import AppIcon from './AppIcon.vue'
import MarkdownContent from './MarkdownContent.vue'
const props = defineProps<{ message: Message; selectedId?: number }>()
const emit = defineEmits<{ close: []; pdf: [citation: Citation] }>()
const showCandidates = ref(false), panel = ref<HTMLElement>()
const items = computed(() => showCandidates.value ? props.message.candidates : props.message.citations)
let previous: HTMLElement | null
async function locate() {
  if (props.selectedId) showCandidates.value = false
  await nextTick()
  const card = panel.value?.querySelector<HTMLElement>(`[data-citation="${props.selectedId}"]`)
  card?.scrollIntoView({ block: 'nearest' }); card?.focus()
}
watch(() => [props.selectedId, props.message.id], locate)
onMounted(async () => { previous = document.activeElement as HTMLElement; panel.value?.querySelector('button')?.focus(); await locate() })
onUnmounted(() => previous?.focus())
function keydown(event: KeyboardEvent) {
  if (event.key === 'Escape') emit('close')
  if (event.key !== 'Tab' || !matchMedia('(max-width:1100px)').matches) return
  const controls = [...panel.value!.querySelectorAll<HTMLElement>('button,summary,a[href]')].filter(el => el.getClientRects().length)
  if (event.shiftKey && document.activeElement === controls[0]) { event.preventDefault(); controls.at(-1)?.focus() }
  if (!event.shiftKey && document.activeElement === controls.at(-1)) { event.preventDefault(); controls[0]?.focus() }
}
</script>
<template>
  <div class="drawer-backdrop evidence-backdrop" @click="emit('close')" />
  <aside ref="panel" class="evidence-panel" aria-label="来源与检索片段" @keydown="keydown">
    <header class="evidence-header"><div><span class="eyebrow">{{ showCandidates ? '本轮候选材料' : '最终回答引用' }}</span><h2>{{ showCandidates ? '检索命中片段' : '已核验引用' }} <span class="count-badge">{{ items.length }}</span></h2></div><button class="icon-button" aria-label="关闭来源面板" @click="emit('close')"><AppIcon name="close" /></button></header>
    <div class="source-tabs"><button :aria-pressed="!showCandidates" @click="showCandidates = false">实际引用 {{ message.citations.length }}</button><button :aria-pressed="showCandidates" @click="showCandidates = true">检索候选 {{ message.candidates.length }}</button></div>
    <div class="evidence-scroll"><p class="evidence-notice">{{ showCandidates ? '候选片段不等于最终答案实际引用。无法核验的片段不会用于已验证引用。' : '已核验编号、片段与PDF物理页对应。请阅读原文判断结论是否得到支持；未提供句子级定位。' }}</p>
      <p v-if="!items.length" class="muted">{{ showCandidates ? '本轮未收到候选片段。' : '本条回答没有已核验引用，可查看检索候选。' }}</p>
      <article v-for="item in items" :key="item.id" class="evidence-card" :class="{ 'selected-citation': selectedId === item.id && !showCandidates }" :data-citation="item.id" tabindex="-1">
        <div class="evidence-card-heading"><span v-if="!showCandidates" class="count-badge">[{{ item.id }}]</span><h3>{{ item.doc_name }}</h3></div>
        <div class="evidence-meta"><span>PDF 物理第 {{ item.page }} 页</span><span v-if="item.page_label">印刷页标签 {{ item.page_label }}</span><span>匹配度未校准</span></div>
        <p v-if="item.unavailable_reason" class="error-text">{{ item.unavailable_reason }}</p>
        <MarkdownContent :content="item.snippet" />
        <div v-if="item.table" class="table-scroll"><table class="source-table"><tbody><tr v-for="(row,i) in item.table.rows" :key="i"><td v-for="(cell,j) in row" :key="j">{{cell}}</td></tr></tbody></table></div>
        <p class="muted">{{ item.included_in_prompt ? '已选入本次生成上下文。' : '未送入本次生成，仅作候选展示。' }}<span v-if="item.truncated"> 此快照为前500字，原片段 {{ item.original_length }} 字。</span></p>
        <button v-if="item.source_verified && item.doc_version" class="secondary-button pdf-open" @click="emit('pdf', item)">查看原文 · 第 {{ item.page }} 页</button>
        <p v-else class="muted">原文关联不可核验，未开放跳转。</p>
        <details><summary>精确证据快照</summary><pre class="raw-text">{{ item.snippet }}</pre></details>
        <details class="debug-details"><summary>调试详情</summary><p>原始分数 {{ item.score ?? '缺失' }}，不代表正确率。</p><p>文档版本 {{ item.doc_version || '未知' }}<br />索引版本 {{ item.index_version }}</p></details>
      </article>
    </div>
  </aside>
</template>
