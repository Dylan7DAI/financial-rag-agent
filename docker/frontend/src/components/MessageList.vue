<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import MarkdownContent from './MarkdownContent.vue'
import AppIcon from './AppIcon.vue'
const props = defineProps({ messages: Array, busy: Boolean })
defineEmits(['evidence', 'citation', 'regenerate', 'copy', 'feedback'])
const scroller = ref(null), follow = ref(true)
const active = computed(() => props.messages.filter(m => m.active || m.role === 'user'))
const latest = computed(() => active.value.filter(m => m.role === 'assistant').at(-1)?.id)
const versions = id => props.messages.filter(m => m.role === 'assistant' && !m.active && m.parent_id === id)
function scroll() { const el = scroller.value; follow.value = el.scrollHeight - el.scrollTop - el.clientHeight < 100 }
watch(() => props.messages.length, () => { follow.value = true })
watch(() => props.messages, async () => { if (follow.value) { await nextTick(); if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight } }, { deep: true })
const phases = { connecting: '正在连接…', retrieving: '正在检索研报…', generating: '正在生成草稿…', repairing: '正在修复引用，上一份草稿已替换…', confirming: '正在确认最终回答…' }
</script>
<template>
  <section ref="scroller" class="conversation-scroll" aria-label="对话消息" @scroll="scroll">
    <div v-if="!messages.length" class="empty-state">
      <div class="empty-symbol"><AppIcon name="book" /></div><span class="eyebrow">从一个好问题开始</span>
      <h2>读懂研报，找到依据。</h2><p>围绕公司、年份与指标提问，<br />点击回答中的引用，核对原始研报。</p>
      <div class="question-guide"><span>提问小贴士</span><strong>公司名称 + 报告年份 + 具体指标</strong><p>示例格式：“某公司某年营业收入是多少？”能否回答取决于当前入库资料。</p></div>
      <div class="empty-footnote"><AppIcon name="file" />本地单用户模式 · AI 回答请结合原文核验</div>
    </div>
    <div v-else class="message-thread">
      <article v-for="message in active" :key="message.id" class="message" :class="`message-${message.role}`">
        <template v-if="message.role === 'user'"><div class="user-label">你</div><div class="user-bubble">{{ message.content }}</div></template>
        <template v-else>
          <div class="assistant-label"><span class="assistant-avatar"><AppIcon name="book" /></span><strong>研知</strong><span>研报助手</span></div>
          <div v-if="message.status === 'pending'" class="draft-label">草稿 · 引用待校验</div>
          <MarkdownContent :content="message.status === 'pending' ? message.draft || '' : message.content"
            :citation-ids="message.status === 'complete' ? message.citations.map(c => c.id) : []"
            @citation="$emit('citation', { message, id: $event })" />
          <div v-if="message.status === 'pending'" class="loading-state" role="status"><span class="activity-dot" />{{ phases[message.phase] || '等待服务响应…' }}</div>
          <div v-if="message.status === 'cancelled'" class="state-note">已停止。此草稿不会用于后续上下文；底层工作可能尚在退出。</div>
          <div v-if="message.status === 'error'" class="message-error" role="alert">{{ message.error || (message.outcome === 'citation_failed' ? '引用未通过校验。请核对材料或重新生成。' : '回答中断，请重试。') }}</div>
          <div v-if="message.outcome === 'no_evidence'" class="state-note">当前材料不足 · 未提供已核验引用</div>
          <details v-if="(message.status === 'error' || message.status === 'cancelled') && message.draft" class="draft-history"><summary>查看未完成草稿（未经核验）</summary><pre class="raw-text">{{ message.draft }}</pre></details>
          <div class="message-tools">
            <button v-if="message.citations.length || message.candidates.length" class="text-button" @click="$emit('evidence', message)"><AppIcon name="file" />来源与片段 <span class="count-badge">{{ message.citations.length }} 引用 / {{ message.candidates.length }} 候选</span></button>
            <button v-if="message.status === 'complete'" class="text-button" @click="$emit('copy', message)">复制</button>
            <button v-if="message.id === latest && message.status !== 'pending'" class="text-button" :disabled="busy" @click="$emit('regenerate', message)">{{ message.status === 'error' ? '重试' : '重新生成' }}</button>
            <template v-if="message.status === 'complete'">
              <button class="text-button" :aria-pressed="message.vote === 1" @click="$emit('feedback', { message, vote: message.vote === 1 ? 0 : 1 })">有帮助</button>
              <button class="text-button" :aria-pressed="message.vote === -1" @click="$emit('feedback', { message, vote: message.vote === -1 ? 0 : -1 })">需改进</button>
            </template>
          </div>
          <details v-if="message.content" class="original-answer"><summary>查看回答原文</summary><pre class="raw-text">{{ message.content }}</pre></details>
          <details v-if="versions(message.parent_id).length" class="answer-versions"><summary>历史回答版本 · {{ versions(message.parent_id).length }}</summary><article v-for="old in versions(message.parent_id)" :key="old.id"><MarkdownContent :content="old.content || old.draft || '此版本未完成'" :citation-ids="old.citations.map(c => c.id)" @citation="$emit('citation', { message: old, id: $event })" /><button v-if="old.candidates.length" class="text-button" @click="$emit('evidence', old)">查看此版本来源</button></article></details>
          <details v-if="message.usage" class="usage-details"><summary>响应详情</summary><p>检索 {{ message.usage.retrieval_ms ?? '未知' }} ms · 首个真实文本 {{ message.usage.first_token_ms ?? '未知' }} ms · 生成 {{ message.usage.generation_ms ?? '未知' }} ms</p><p>输入 {{ message.usage.input_tokens ?? '未知' }} tokens · 输出 {{ message.usage.output_tokens ?? '未知' }} tokens</p></details>
        </template>
      </article>
    </div>
  </section>
</template>
