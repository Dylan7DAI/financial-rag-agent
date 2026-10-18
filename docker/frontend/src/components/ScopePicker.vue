<script setup>
import {ref,onMounted,onBeforeUnmount} from 'vue'
import {request} from '../lib/apiV2'
defineProps({disabled:Boolean})
const emit=defineEmits(['change']),knowledge=ref(null),error=ref('')
let timer
async function load(){try{knowledge.value=await request('/knowledge-base');error.value='';emit('change',{kb_ids:[knowledge.value.id]})}catch(e){error.value=e.message}}
onMounted(()=>{void load();timer=setInterval(()=>{if(knowledge.value?.processing)void load()},2500)})
onBeforeUnmount(()=>clearInterval(timer))
</script>
<template><div class="scope-picker single-knowledge-scope"><span v-if="knowledge">知识库：{{knowledge.name}} <span class="muted">· {{knowledge.ready_count}} 份 PDF 可检索<span v-if="knowledge.processing">，其余处理中</span></span></span><span v-else-if="error" role="alert">{{error}}</span><span v-else role="status">正在读取知识库…</span></div></template>
