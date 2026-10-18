<script setup lang="ts">
import {ref,nextTick,onMounted,onBeforeUnmount} from 'vue'
import {request} from '../lib/apiV2'
import type {KnowledgeBase,KnowledgePdf} from '../lib/contracts'
const knowledge=ref<KnowledgeBase|null>(null),files=ref<KnowledgePdf[]>([]),total=ref(0),offset=ref(0)
const loading=ref(true),error=ref(''),notice=ref(''),editing=ref(false),saving=ref(false),draft=ref(''),nameInput=ref<HTMLInputElement>()
let timer:ReturnType<typeof setInterval>|undefined
async function load(){error.value='';loading.value=true;try{const [base,result]=await Promise.all([request<KnowledgeBase>('/knowledge-base'),request<{total:number;items:KnowledgePdf[]}>(`/knowledge-base/documents?offset=${offset.value}&limit=20`)]);knowledge.value=base;files.value=result.items;total.value=result.total}catch(e){error.value=(e as Error).message}finally{loading.value=false}}
async function startRename(){draft.value=knowledge.value?.name||'';editing.value=true;notice.value='';await nextTick();nameInput.value?.focus();nameInput.value?.select()}
async function rename(){if(!draft.value.trim())return;saving.value=true;error.value='';try{knowledge.value=await request<KnowledgeBase>('/knowledge-base','PATCH',{name:draft.value.trim()});editing.value=false;notice.value='知识库名称已更新'}catch(e){error.value=(e as Error).message}finally{saving.value=false}}
async function paginate(step:number){offset.value+=step;await load()}
onMounted(()=>{void load();timer=setInterval(()=>{if(knowledge.value?.processing&&!loading.value)void load()},2500)})
onBeforeUnmount(()=>clearInterval(timer))
</script>
<template><main class="management-page single-knowledge-page"><header class="single-knowledge-heading"><div><span class="eyebrow">知识库</span><h1>{{knowledge?.name||'研报知识库'}}</h1><p>文档目录中的全部 PDF 统一加入此知识库。</p></div><button v-if="!editing" class="secondary-button" :disabled="!knowledge" @click="startRename">重命名知识库</button></header>
  <form v-if="editing" class="knowledge-rename" @submit.prevent="rename" @keydown.esc="!saving&&(editing=false)"><label for="knowledge-name">知识库名称</label><input id="knowledge-name" ref="nameInput" v-model="draft" maxlength="80" required :disabled="saving"/><div><button type="button" class="secondary-button" :disabled="saving" @click="editing=false">取消</button><button class="primary-button" :disabled="saving||!draft.trim()">{{saving?'保存中…':'保存名称'}}</button></div></form>
  <p v-if="error" class="app-error" role="alert">{{error}}</p><p v-if="notice" class="app-notice" role="status">{{notice}}</p>
  <section class="pdf-catalog"><div class="pdf-catalog-title"><h2>参与 RAG 的 PDF 文件 <span class="count-badge">{{total}}</span></h2><button class="text-button" :disabled="loading" @click="load">刷新文件清单</button></div><p v-if="knowledge?.processing" class="state-note" role="status">正在处理文档，完成后将自动参与检索。当前已可检索 {{knowledge.ready_count}} / {{knowledge.document_count}} 份。</p><p v-if="loading&&!files.length" role="status">正在读取 PDF 文件清单…</p>
  <div v-else-if="files.length" class="table-scroll"><table class="document-table"><thead><tr><th>PDF 文件</th><th>页数</th><th>RAG 状态</th></tr></thead><tbody><tr v-for="file in files" :key="file.id"><td>{{file.name}}</td><td>{{file.pages||'—'}}</td><td>{{file.participates_in_rag?'已参与 RAG':file.state==='failed'?'处理失败':'处理中'}}<small v-if="file.participates_in_rag&&!file.original_available">原文文件暂不可用</small><small v-if="file.error" class="error-text">{{file.error}}</small></td></tr></tbody></table></div><p v-else-if="!error" class="empty-table">文档目录中暂无 PDF 文件。</p>
  <div v-if="total>20" class="pagination"><button :disabled="!offset||loading" @click="paginate(-20)">上一页</button><span>{{offset+1}}–{{Math.min(offset+20,total)}} / {{total}}</span><button :disabled="offset+20>=total||loading" @click="paginate(20)">下一页</button></div></section>
</main></template>
