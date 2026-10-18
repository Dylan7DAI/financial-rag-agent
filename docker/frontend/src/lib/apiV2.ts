import { createSSEParser } from './sse.js'
import type { Session, StreamEvent } from './contracts'

const root = (import.meta.env?.VITE_API_BASE_URL || '').replace(/\/+$/, '') + '/api/v2'
export async function request<T = unknown>(path: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(root + path, { method, signal: signal || AbortSignal.timeout(30_000), headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
  if (!response.ok) {
    const data = await response.json().catch(() => null)
    throw new Error(typeof data?.detail === 'string' ? data.detail : `请求失败（HTTP ${response.status}），请稍后重试。`)
  }
  return response.json()
}
export const sessionAPI = {
  list: (q = '') => request<Session[]>('/sessions?q=' + encodeURIComponent(q)),
  create: () => request<Session>('/sessions', 'POST'),
  get: (id: string) => request<Session>('/sessions/' + id),
  rename: (id: string, title: string) => request<Session>('/sessions/' + id, 'PATCH', { title }),
  remove: (id: string) => request('/sessions/' + id, 'DELETE'),
  clear: (id: string) => request('/sessions/' + id + '/clear', 'POST'),
}
export const pdfURL = (id: string, version: string, page: number) => `${root}/documents/${encodeURIComponent(id)}/pdf?version=${encodeURIComponent(version)}&page=${page}`

export async function streamV2(body: Record<string, unknown>, callback: (event: StreamEvent) => void, signal: AbortSignal) {
  const response = await fetch(root + '/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal })
  if (!response.ok) {
    const result = await response.json().catch(() => null)
    throw new Error(typeof result?.detail === 'string' ? result.detail : `请求失败（HTTP ${response.status}）`)
  }
  if (!response.body) throw new Error('服务没有返回响应内容。')
  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8', { fatal: true })
  let terminal = false, final = false, sequence = 0, messageId = ''
  const feed = createSSEParser((event: StreamEvent) => {
    if (signal.aborted) return
    if (event.protocol !== 2 || event.request_id !== body.request_id || event.session_id !== body.session_id || event.seq !== sequence + 1 || !event.message_id) throw new Error('响应归属或事件顺序异常。')
    if (messageId && messageId !== event.message_id) throw new Error('回答消息标识发生变化。')
    if (terminal) throw new Error('收到重复终态。')
    if (!['status','delta','citations','final','done','error'].includes(event.type)) throw new Error('收到未知协议事件。')
    if (event.type === 'delta' && typeof event.text !== 'string') throw new Error('增量文本格式异常。')
    messageId = event.message_id; sequence = event.seq
    if (event.type === 'final') {
      if (final) throw new Error('收到重复最终回答。')
      if (typeof event.content !== 'string' || !Array.isArray(event.citations) || !Array.isArray(event.candidates)) throw new Error('最终回答格式异常。')
      if (event.outcome === 'answered' && (event.citation_integrity !== 'passed' || !event.citations.length || event.citations.some(c => !c.source_verified || !Number.isInteger(c.id) || c.id < 1))) throw new Error('最终回答缺少通过校验的引用。')
      final = true
    }
    if (event.type === 'done') {
      if (event.outcome !== 'cancelled' && !final) throw new Error('响应缺少经过确认的最终回答。')
      terminal = true
    }
    if (event.type === 'error') terminal = true
    callback(event)
  })
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      feed(decoder.decode(value, { stream: true }))
    }
    feed(decoder.decode(), true)
    if (!signal.aborted && !terminal) throw new Error('连接提前结束，回答尚未完成确认。')
  } finally {
    try { await reader.cancel() } catch { /* disconnected */ }
    reader.releaseLock()
  }
}
