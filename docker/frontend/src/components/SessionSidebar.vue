<script setup>
import AppIcon from './AppIcon.vue'
import { nextTick, ref, watch } from 'vue'
const props = defineProps({ sessions: Array, currentId: String, open: Boolean, search: String })
defineEmits(['select', 'create', 'close', 'search'])
const sidebar = ref(null)
let previous
watch(() => props.open, async open => {
  if (open) { previous = document.activeElement; await nextTick(); sidebar.value?.querySelector('button')?.focus() }
  else previous?.focus()
})
function trap(event) {
  if (event.key !== 'Tab' || !props.open || !matchMedia('(max-width:700px)').matches) return
  const controls = [...sidebar.value.querySelectorAll('button,input')].filter(el => el.getClientRects().length)
  if (event.shiftKey && document.activeElement === controls[0]) { event.preventDefault(); controls.at(-1)?.focus() }
  if (!event.shiftKey && document.activeElement === controls.at(-1)) { event.preventDefault(); controls[0]?.focus() }
}
</script>
<template>
  <aside ref="sidebar" class="session-sidebar" :class="{ 'is-open': open }" aria-label="会话列表" @keydown.esc="$emit('close')" @keydown="trap">
    <div class="brand"><div class="brand-mark"><AppIcon name="book" /></div><div><strong>研知</strong><span>FINANCIAL RESEARCH</span></div><button class="icon-button sidebar-close" aria-label="关闭会话菜单" @click="$emit('close')"><AppIcon name="close" /></button></div>
    <button class="primary-button new-session" @click="$emit('create')"><AppIcon name="plus" />新建会话</button>
    <input class="session-search" :value="search" aria-label="搜索会话" placeholder="搜索标题或问题…" @input="$emit('search', $event.target.value)" />
    <div class="sidebar-label">已保存会话 <span>{{ sessions.length }}</span></div>
    <nav class="session-list"><button v-for="session in sessions" :key="session.id" class="session-item" :class="{ active: session.id === currentId }" :aria-current="session.id === currentId ? 'page' : undefined" @click="$emit('select', session.id)"><AppIcon name="chat" /><span>{{ session.title }}</span><i v-if="session.pending" class="activity-dot" aria-label="正在回答" /></button><p v-if="!sessions.length" class="muted">未找到匹配会话</p></nav>
    <div class="sidebar-note"><AppIcon name="file" /><strong>让每次阅读，更进一步</strong><p>点击引用，核对原文。校验编号与来源并不代表结论必然正确。</p></div>
    <footer class="sidebar-footer">会话保存在本机服务端，刷新后可继续阅读。</footer>
  </aside>
</template>
