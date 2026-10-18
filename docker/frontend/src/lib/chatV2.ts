import { reactive } from 'vue'
import { sessionAPI, request, streamV2 } from './apiV2'
import type { Message, Session, StreamEvent, RetrievalScope } from './contracts'

interface LocalSession extends Session { messages: Message[]; draft: string; pending: boolean }
type Handle = { controller: AbortController; requestId: string; messageId: string; stopped: boolean }
const uid = () => {
  if (crypto.randomUUID) return crypto.randomUUID()
  const bytes = crypto.getRandomValues(new Uint8Array(16)); bytes[6] = (bytes[6]! & 15) | 64; bytes[8] = (bytes[8]! & 63) | 128
  const h = [...bytes].map(x => x.toString(16).padStart(2, '0')).join('')
  return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20)}`
}
const local = (s: Session): LocalSession => ({ ...s, messages: s.messages || [], draft: '', pending: (s.messages || []).some(m => m.status === 'pending') })

export function createChatV2() {
  const state = reactive({ sessions: [] as LocalSession[], currentId: '', loading: true, error: '', search: '', notice: '', scope: {} as RetrievalScope })
  const handles = new Map<string, Handle>()
  let searchSerial = 0
  const polls = new Map<string, ReturnType<typeof setTimeout>>()
  function merge(s: Session) {
    const current = state.sessions.find(item => item.id === s.id)
    if (current) Object.assign(current, { ...s, messages: s.messages || current.messages })
    else state.sessions.unshift(local(s))
    return state.sessions.find(item => item.id === s.id)!
  }
  async function load(id: string) {
    const session = merge(await sessionAPI.get(id))
    session.pending = session.messages.some(m => m.status === 'pending')
    if (polls.has(id)) { clearTimeout(polls.get(id)); polls.delete(id) }
    if (session.pending && !handles.has(id)) {
      polls.set(id, setTimeout(() => { void load(id).catch(() => {}) }, 1500))
    }
    return session
  }
  async function create() {
    state.search = ''
    const session = merge(await sessionAPI.create())
    state.currentId = session.id
    localStorage.setItem('research-current-session', session.id)
    return session
  }
  async function initialize() {
    try {
      const sessions = await sessionAPI.list()
      state.sessions = sessions.map(local)
      const remembered = localStorage.getItem('research-current-session')
      const selected = sessions.find(s => s.id === remembered) || sessions[0]
      if (selected) await select(selected.id)
      else await create()
    } catch (error) { state.error = (error as Error).message }
    finally { state.loading = false }
  }
  async function select(id: string) {
    state.error = ''
    await load(id)
    state.currentId = id
    localStorage.setItem('research-current-session', id)
  }
  async function search(q: string) {
    const serial = ++searchSerial
    const sessions = await sessionAPI.list(q)
    if (serial !== searchSerial) return
    state.search = q
    // Keep local running state/drafts; filtered IDs are managed separately by the caller.
    for (const s of sessions) merge(s)
    return sessions.map(s => s.id)
  }
  function messageFor(session: LocalSession, handle: Handle) { return session.messages.find(m => m.id === handle.messageId) }
  async function run(session: LocalSession, target?: Message) {
    if (session.pending || handles.has(session.id)) return
    const query = target?.query || session.draft.trim()
    if (!query) return
    state.error = ''; session.pending = true
    const handle = { controller: new AbortController(), requestId: uid(), messageId: 'local-' + uid(), stopped: false }
    handles.set(session.id, handle)
    const placeholder: Message = { id: handle.messageId, session_id: session.id, role: 'assistant', status: 'pending',
      content: '', draft: '', citations: [], candidates: [], active: true, query, created_at: new Date().toISOString(), phase: 'connecting' }
    if (!target) {
      const userId = 'local-user-' + uid()
      placeholder.parent_id = userId
      session.messages.push({ id: userId, session_id: session.id, role: 'user', content: query, status: 'complete', active: true, citations: [], candidates: [], created_at: placeholder.created_at })
    }
    session.messages.push(placeholder)
    if (!target) session.draft = ''
    let accepted = false
    const timer = setTimeout(() => { void stop(session) }, 250_000)
    try {
      await streamV2({ session_id: session.id, request_id: handle.requestId, ...(target ? { regenerate_message_id: target.id } : { query, scope: state.scope }) }, (event: StreamEvent) => {
        if (handle.stopped || handles.get(session.id) !== handle) return
        if (event.type === 'status' && event.assistant_message && event.user_message) {
          accepted = true
          session.messages = session.messages.filter(m => m.id !== handle.messageId && m.id !== placeholder.parent_id && m.id !== event.assistant_message!.id)
          if (target) { const old = session.messages.find(m => m.id === target.id); if (old) old.active = false }
          if (!session.messages.some(m => m.id === event.user_message!.id)) session.messages.push(event.user_message)
          session.messages.push({ ...event.assistant_message, draft: '', phase: event.phase })
          handle.messageId = event.message_id
          if (!target && session.title === '新会话') session.title = query.slice(0,32)
        }
        const message = messageFor(session, handle)
        if (!message) return
        if (event.type === 'status') { message.phase = event.phase; if (event.reset_draft) message.draft = '' }
        if (event.type === 'delta') message.draft = (message.draft || '') + event.text
        if (event.type === 'citations') message.candidates = event.candidates || []
        if (event.type === 'final') {
          Object.assign(message, { content: event.content || '', draft: '', citations: event.citations || [], candidates: event.candidates || [], usage: event.usage, outcome: event.outcome, citation_integrity: event.citation_integrity, phase: 'confirming' })
        }
        if (event.type === 'done') {
          message.status = event.outcome === 'cancelled' ? 'cancelled' : event.outcome === 'citation_failed' ? 'error' : 'complete'
          message.outcome = event.outcome
        }
        if (event.type === 'error') { message.status = 'error'; message.error = event.message || '服务处理失败。' }
      }, handle.controller.signal)
      if (!accepted && !handle.stopped) throw new Error('服务未确认消息。')
    } catch (error) {
      if (!handle.stopped) {
        const message = messageFor(session, handle)
        if (message) { message.status = 'error'; message.error = (error as Error).message || '连接中断，请重试。' }
        // Closing a broken stream also asks the server to settle the durable request.
        await request('/requests/' + handle.requestId + '/cancel', 'POST').catch(() => {})
        if (!accepted) {
          // A connection may fail after the server committed the user but before
          // its first status frame arrived. Recover its real ID before retrying.
          const recovered = await request<{ session_id: string; message: Message }>('/requests/' + handle.requestId).catch(() => null)
          if (recovered?.session_id === session.id) await load(session.id).catch(() => {})
        }
      }
    } finally {
      clearTimeout(timer)
      if (handles.get(session.id) === handle) handles.delete(session.id)
      session.pending = false
      // Preserve local network errors; accepted server outcomes can be recovered on reload.
      if (handle.stopped) await load(session.id).catch(() => {})
    }
  }
  async function stop(session: LocalSession) {
    const handle = handles.get(session.id)
    if (!handle) {
      const pending = session.messages.find(m => m.status === 'pending' && m.request_id)
      if (pending) { await request('/requests/' + pending.request_id + '/cancel', 'POST'); await load(session.id) }
      return
    }
    if (handle.stopped) return
    handle.stopped = true
    handle.controller.abort()
    try { await request('/requests/' + handle.requestId + '/cancel', 'POST', undefined, AbortSignal.timeout(10_000)) }
    catch { state.error = '取消确认失败；连接已中断，请刷新查看服务端状态。' }
    await load(session.id).catch(() => {})
  }
  async function clear(session: LocalSession, remove = false) {
    if (session.pending) throw new Error('请先停止当前回答。')
    if (polls.has(session.id)) { clearTimeout(polls.get(session.id)); polls.delete(session.id) }
    if (remove) {
      await sessionAPI.remove(session.id)
      state.sessions = state.sessions.filter(s => s.id !== session.id)
      if (!state.sessions.length) await create()
      else await select(state.sessions[0]!.id)
    } else { await sessionAPI.clear(session.id); await load(session.id) }
  }
  async function rename(session: LocalSession, title: string) { merge(await sessionAPI.rename(session.id, title)) }
  async function feedback(message: Message, vote: number, reason = '') {
    const result = await request<Message>('/messages/' + message.id + '/feedback', 'POST', { vote, reason })
    Object.assign(message, result)
  }
  async function copy(message: Message) {
    await navigator.clipboard.writeText(message.content)
    await request('/messages/' + message.id + '/copy', 'POST')
    state.notice = '已复制并记录'
  }
  async function retry(session: LocalSession, message: Message) {
    if (message.id.startsWith('local-')) { session.messages = session.messages.filter(m => m.id !== message.id && m.id !== message.parent_id); session.draft = message.query || ''; return run(session) }
    return run(session, message)
  }
  return { state, initialize, create, select, search, run, retry, stop, clear, rename, feedback, copy }
}
