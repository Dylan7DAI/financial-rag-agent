<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { getDocument, GlobalWorkerOptions, type PDFDocumentProxy, type RenderTask } from 'pdfjs-dist'
import workerURL from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import type { Citation } from '../lib/contracts'
import { pdfURL } from '../lib/apiV2'
import AppIcon from './AppIcon.vue'
import { viewportRegion } from '../lib/pdfRegions'
GlobalWorkerOptions.workerSrc = workerURL
const props = defineProps<{ citation: Citation }>()
const emit = defineEmits<{ close: [] }>()
const canvas = ref<HTMLCanvasElement>(), container = ref<HTMLElement>(), dialog = ref<HTMLElement>()
const loading = ref(true), error = ref(''), pageNumber = ref(props.citation.page), pageCount = ref(0)
const zoom=ref(1), rotation=ref(0), highlighted=ref(0)
let document: PDFDocumentProxy | undefined, renderTask: RenderTask | undefined
let loadingTask: ReturnType<typeof getDocument> | undefined
let sequence = 0, controller: AbortController | undefined, previous: HTMLElement | null, observer: ResizeObserver | undefined
async function render() {
  if (!document || !canvas.value || !container.value) return
  const number = Number(pageNumber.value)
  if (!Number.isInteger(number) || number < 1 || number > pageCount.value) { error.value = '物理页码超出文档范围。'; return }
  const token = ++sequence
  renderTask?.cancel()
  await renderTask?.promise.catch(() => {})
  if (token !== sequence) return
  error.value = ''
  try {
    const page = await document.getPage(number)
    if (token !== sequence) return
    const viewport = page.getViewport({ scale: 1, rotation:(page.rotate+rotation.value)%360 })
    const scale = Math.min((container.value.clientWidth - 32) / viewport.width, 1.7)
    const sized = page.getViewport({ scale: Math.max(.2, scale)*zoom.value, rotation:(page.rotate+rotation.value)%360 })
    const ratio = window.devicePixelRatio || 1
    canvas.value.width = Math.floor(sized.width * ratio); canvas.value.height = Math.floor(sized.height * ratio)
    canvas.value.style.width = `${sized.width}px`; canvas.value.style.height = `${sized.height}px`
    renderTask = page.render({ canvas: canvas.value, viewport: sized, transform: ratio === 1 ? undefined : [ratio,0,0,ratio,0,0] })
    await renderTask.promise
    highlighted.value=0
    const geometry=props.citation.geometry
    if(number===props.citation.page && geometry?.coordinate_space==='pymupdf-unrotated'){
      const context=canvas.value.getContext('2d')!
      context.save();context.scale(ratio,ratio)
      for(const box of props.citation.regions||[]){
        const r=viewportRegion(box,geometry.transformation_matrix,(x,y)=>sized.convertToViewportPoint(x,y),sized.width,sized.height)
        if(!r)continue
        context.fillStyle='rgba(255,193,7,.22)';context.strokeStyle='#bc7900';context.lineWidth=1.5
        context.fillRect(r[0]!,r[1]!,r[2]!-r[0]!,r[3]!-r[1]!);context.strokeRect(r[0]!,r[1]!,r[2]!-r[0]!,r[3]!-r[1]!);highlighted.value++
      }
      context.restore()
    }
    canvas.value.dataset.renderedPage = String(number)
  } catch (cause) { if (token === sequence && (cause as Error).name !== 'RenderingCancelledException') error.value = 'PDF 页面无法渲染，请重试。' }
}
async function load() {
  ++sequence; renderTask?.cancel(); controller?.abort(); await loadingTask?.destroy(); document = undefined
  loading.value = true; error.value = ''; pageNumber.value = props.citation.page
  controller = new AbortController()
  try {
    const response = await fetch(pdfURL(props.citation.doc_id, props.citation.doc_version!, props.citation.page), { signal: controller.signal })
    if (!response.ok) {
      const info = await response.json().catch(() => null)
      throw new Error(info?.detail || '原文不可用，可能已移除或版本已变化。')
    }
    loadingTask = getDocument({ data: new Uint8Array(await response.arrayBuffer()), enableXfa: false })
    document = await loadingTask.promise
    pageCount.value = document.numPages
    await nextTick(); await render()
  } catch (cause) { if ((cause as Error).name !== 'AbortError') error.value = (cause as Error).message }
  finally { loading.value = false }
}
function trap(event: KeyboardEvent) {
  if (event.key === 'Escape') emit('close')
  if (event.key !== 'Tab') return
  const controls = [...dialog.value!.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled)')]
  if (event.shiftKey && globalThis.document.activeElement === controls[0]) { event.preventDefault(); controls.at(-1)?.focus() }
  if (!event.shiftKey && globalThis.document.activeElement === controls.at(-1)) { event.preventDefault(); controls[0]?.focus() }
}
onMounted(async () => {
  previous = globalThis.document.activeElement as HTMLElement
  dialog.value?.querySelector('button')?.focus()
  observer = new ResizeObserver(() => { void render() }); if (container.value) observer.observe(container.value)
  await load()
})
watch(() => [props.citation.doc_id, props.citation.doc_version, props.citation.page], load)
watch(pageNumber, render)
watch([zoom,rotation],render)
onBeforeUnmount(() => { ++sequence; controller?.abort(); renderTask?.cancel(); void loadingTask?.destroy(); observer?.disconnect(); previous?.focus() })
</script>
<template>
  <div class="pdf-backdrop" @click.self="emit('close')">
    <section ref="dialog" class="pdf-drawer" role="dialog" aria-modal="true" aria-labelledby="pdf-title" @keydown="trap">
      <header class="pdf-header"><div><span class="eyebrow">原始 PDF · 页级核验</span><h2 id="pdf-title">{{ citation.doc_name }}</h2></div><button class="icon-button" aria-label="关闭PDF原文" @click="emit('close')"><AppIcon name="close" /></button></header>
      <div class="pdf-navigation"><button class="secondary-button" :disabled="loading || pageNumber <= 1" @click="pageNumber--">上一页</button><label>物理页 <input v-model.number="pageNumber" type="number" min="1" :max="pageCount" aria-label="PDF物理页码" /> / {{ pageCount || '…' }}</label><button class="secondary-button" :disabled="loading || pageNumber >= pageCount" @click="pageNumber++">下一页</button></div>
      <div class="pdf-navigation"><label>缩放<select v-model.number="zoom"><option :value=".75">75%</option><option :value="1">适应宽度</option><option :value="1.5">150%</option><option :value="2">200%</option></select></label><button class="secondary-button" @click="rotation=(rotation+90)%360">旋转 90°</button></div>
      <p class="pdf-note">{{highlighted?`已标出 ${highlighted} 个真实解析元素区域；框为文本块/表格范围，不代表逐句命中。`:'无可用区域信息，仅定位正确原页。'}} 印刷页标签可能与物理页不同。</p>
      <div ref="container" class="pdf-canvas-scroll"><p v-if="loading" role="status">正在加载原文…</p><div v-if="error" role="alert" class="message-error">{{ error }}<button class="text-button" @click="load">重新加载</button></div><canvas v-show="!error" ref="canvas" aria-label="PDF原始页面" /></div>
    </section>
  </div>
</template>
