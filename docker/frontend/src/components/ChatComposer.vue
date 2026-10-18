<script setup>
import { computed, ref } from 'vue'
import AppIcon from './AppIcon.vue'
const props = defineProps({ modelValue: String, busy: Boolean })
const emit = defineEmits(['update:modelValue', 'send', 'stop'])
const composing = ref(false)
const canSend = computed(() => !props.busy && Boolean(props.modelValue.trim()))
function keydown(event) {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing || composing.value || event.keyCode === 229) return
  event.preventDefault()
  if (canSend.value) emit('send')
}
</script>
<template>
  <div class="composer-area">
    <form class="composer" @submit.prevent="canSend && $emit('send')">
      <label class="sr-only" for="question">输入问题</label>
      <textarea id="question" :value="modelValue" rows="3" maxlength="2000" placeholder="输入你想了解的研报问题…"
        @input="$emit('update:modelValue', $event.target.value)" @keydown="keydown" @compositionstart="composing = true" @compositionend="composing = false" />
      <div class="composer-bottom"><span><AppIcon name="file" />基于已入库研报</span><button v-if="busy" class="stop-button" type="button" aria-label="停止生成" @click="$emit('stop')">停止</button><button v-else class="send-button" type="submit" :disabled="!canSend" aria-label="发送问题"><AppIcon name="arrow" /></button></div>
    </form>
    <div class="composer-hint"><span>Enter 发送 · Shift + Enter 换行</span><span>AI 回答可能有误，请核验来源</span></div>
  </div>
</template>
