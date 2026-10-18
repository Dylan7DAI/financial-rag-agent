// Dispatch complete frames only: chunks may end inside UTF-8, CRLF or JSON.
export function createSSEParser(onEvent) {
  let buffer = ''
  return (text, final = false) => {
    buffer += text
    let boundary
    while ((boundary = /\r\n\r\n|\n\n|\r\r/.exec(buffer))) {
      const frame = buffer.slice(0, boundary.index)
      buffer = buffer.slice(boundary.index + boundary[0].length)
      const data = frame.split(/\r\n|\n|\r/)
        .filter(line => line.startsWith('data:') || line === 'data')
        .map(line => line === 'data' ? '' : line.slice(5).replace(/^ /, '')).join('\n')
      if (!data.trim()) continue // comments / heartbeat
      let event
      try { event = JSON.parse(data) } catch { throw new Error('服务返回的数据格式异常，请重试。') }
      if (!event || typeof event !== 'object') throw new Error('服务返回了无效事件，请重试。')
      onEvent(event)
    }
    if (final && buffer.split(/\r\n|\n|\r/).some(line => line.trim() && !line.startsWith(':'))) {
      throw new Error('响应意外中断，数据未完整接收。请重试。')
    }
  }
}
