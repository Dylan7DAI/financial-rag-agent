import MarkdownIt from 'markdown-it'

export const markdown = new MarkdownIt({ html: false, linkify: true, breaks: true })
markdown.inline.ruler.before('link', 'verified_citation', (state, silent) => {
  const match = /^\[(\d+)\](?!\()/.exec(state.src.slice(state.pos))
  if (!match || !state.env?.citationIds?.includes(Number(match[1]))) return false
  if (!silent) {
    const token = state.push('verified_citation', '', 0)
    token.content = match[1]
  }
  state.pos += match[0].length
  return true
})
markdown.renderer.rules.verified_citation = (tokens, index) => {
  const id = Number(tokens[index].content)
  return `<button type="button" class="citation-button" data-citation-id="${id}" aria-label="查看引用 ${id}">[${id}]</button>`
}
// Accept only this presentation tag. Code spans/fences remain untouched.
markdown.inline.ruler.before('html_inline', 'safe_break', (state, silent) => {
  const match = /^<br\s*\/?\s*>/i.exec(state.src.slice(state.pos))
  if (!match) return false
  if (!silent) state.push('hardbreak', 'br', 0)
  state.pos += match[0].length
  return true
})
markdown.validateLink = url => /^(https?:\/\/|mailto:|#)/i.test(url)
// Do not load model-supplied remote images.
markdown.renderer.rules.image = (tokens, index) => markdown.utils.escapeHtml(tokens[index].content)

export function splitAnswerSources(raw) {
  const original = String(raw ?? '')
  const lines = original.replace(/\r\n/g, '\n').split('\n')
  const codeLines = new Set()
  for (const token of markdown.parse(original, {})) {
    if ((token.type === 'fence' || token.type === 'code_block') && token.map) {
      for (let i = token.map[0]; i < token.map[1]; i++) codeLines.add(i)
    }
  }
  for (let i = lines.length - 1; i >= 0; i--) {
    if (codeLines.has(i) || !/^(?:#{1,6}\s+)?(?:\*\*)?来源[：:]?(?:\*\*)?[：:]?\s*$/.test(lines[i])) continue
    const tail = lines.slice(i + 1).filter(line => line.trim())
    // Only extract a terminal list of PDF names and explicit positive page numbers.
    if (!tail.length || tail.some(line => !/^[-*]\s+[^<>\n]+\.pdf[，,]\s*第[1-9]\d*页\s*$/i.test(line))) break
    if (lines.slice(i + 1).some((line, n) => line.trim() && codeLines.has(i + 1 + n))) break
    const body = lines.slice(0, i).join('\n').trimEnd()
    if (!body.trim()) break
    return { body, sources: tail.map(line => line.replace(/^[-*]\s+/, '')), original }
  }
  return { body: original, sources: [], original }
}
