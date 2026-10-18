<script setup>
import {ref,onMounted,onBeforeUnmount,watch} from 'vue'
import {request} from './lib/apiV2'
import ChatWorkspace from './ChatWorkspace.vue'
import KnowledgePanel from './components/KnowledgePanel.vue'
import TracePanel from './components/TracePanel.vue'
const ready=ref(false),error=ref(''),page=ref('chat')
const theme=ref(localStorage.getItem('research-theme')||'system'),media=matchMedia('(prefers-color-scheme: dark)')
function applyTheme(){document.documentElement.dataset.theme=theme.value==='system'?(media.matches?'dark':'light'):theme.value;localStorage.setItem('research-theme',theme.value)}
watch(theme,applyTheme);media.addEventListener('change',applyTheme);applyTheme()
onBeforeUnmount(()=>media.removeEventListener('change',applyTheme))
async function connect(){error.value='';try{await request('/knowledge-base');ready.value=true}catch(e){error.value=e.message}}
onMounted(connect)
</script>
<template><div class="beta-shell">
  <header class="beta-nav"><strong>研报问答 <small>Beta</small></strong><nav v-if="ready" aria-label="工作区导航"><button :aria-current="page==='chat'?'page':undefined" @click="page='chat'">问答</button><button :aria-current="page==='knowledge'?'page':undefined" @click="page='knowledge'">知识库</button><button :aria-current="page==='traces'?'page':undefined" @click="page='traces'">记录与反馈</button></nav><label class="theme-picker"><span class="sr-only">外观主题</span><select v-model="theme" aria-label="外观主题"><option value="system">跟随系统</option><option value="light">浅色</option><option value="dark">深色</option></select></label></header>
  <main v-if="!ready" class="startup-state"><div v-if="error"><p role="alert">{{error}}</p><button class="primary-button" @click="connect">重新连接</button></div><p v-else role="status">正在连接知识库…</p></main>
  <template v-else><ChatWorkspace v-if="page==='chat'"/><KnowledgePanel v-else-if="page==='knowledge'"/><TracePanel v-else/></template>
</div></template>
