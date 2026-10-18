import test from 'node:test'
import assert from 'node:assert/strict'
import {createSSEParser} from '../src/lib/sse.js'
import {request,streamV2} from '../src/lib/apiV2.ts'
import {markdown,splitAnswerSources} from '../src/lib/markdown.js'

const body={request_id:'request-fixture',session_id:'session-fixture'}
const event=(seq,type,extra={})=>({protocol:2,...body,message_id:'message-fixture',seq,type,...extra})
const final=seq=>event(seq,'final',{content:'UI_FIXTURE 收入 125 [1]',outcome:'answered',citation_integrity:'passed',
  citations:[{id:1,source_verified:true}],candidates:[]})
function response(events,{truncate=false,bytewise=false}={}){
  const bytes=new TextEncoder().encode(events.map(e=>'data: '+JSON.stringify(e)+'\r\n\r\n').join('')+(truncate?'data: {':''))
  return new Response(new ReadableStream({start(controller){
    if(bytewise)for(const byte of bytes)controller.enqueue(Uint8Array.of(byte));else controller.enqueue(bytes)
    controller.close()
  }}),{headers:{'Content-Type':'text/event-stream'}})
}

test('SSE preserves all chunk boundaries, CRLF, multiline data and heartbeats',()=>{
  const input=': ping\r\n\r\ndata: {"value":\r\ndata: "中文 12.30 亿元"}\r\n\r\n'
  for(let boundary=0;boundary<=input.length;boundary++){
    const events=[],feed=createSSEParser(e=>events.push(e))
    feed(input.slice(0,boundary));feed(input.slice(boundary));feed('',true)
    assert.deepEqual(events,[{value:'中文 12.30 亿元'}])
  }
})

test('SSE rejects malformed JSON and partial terminal frames',()=>{
  assert.throws(()=>createSSEParser(()=>{})('data: invalid\n\n'),/格式/)
  const feed=createSSEParser(()=>{});feed('data: {');assert.throws(()=>feed('',true),/中断/)
})

test('production v2 stream decodes single-byte UTF-8 and accepts one final/done',async t=>{
  t.mock.method(globalThis,'fetch',async()=>response([event(1,'delta',{text:'中文'}),final(2),event(3,'done',{outcome:'answered'})],{bytewise:true}))
  const events=[];await streamV2(body,e=>events.push(e),new AbortController().signal)
  assert.equal(events[0].text,'中文');assert.equal(events[1].content,'UI_FIXTURE 收入 125 [1]')
  assert.deepEqual(events.map(e=>e.type),['delta','final','done'])
})

test('v2 rejects wrong identities, sequence gaps and unverified final citations',async t=>{
  for(const events of [[event(2,'status')],[event(1,'status',{session_id:'other'})],[final(1),event(2,'final')],
    [event(1,'final',{content:'bad',outcome:'answered',citations:[],candidates:[]})]]){
    const mock=t.mock.method(globalThis,'fetch',async()=>response(events))
    await assert.rejects(streamV2(body,()=>{},new AbortController().signal));mock.mock.restore()
  }
})

test('v2 disconnect/missing final is recoverable; cancelled streams need no final',async t=>{
  const mock=t.mock.method(globalThis,'fetch',async()=>response([event(1,'delta',{text:'draft'})]))
  await assert.rejects(streamV2(body,()=>{},new AbortController().signal),/提前结束/)
  mock.mock.restore()
  t.mock.method(globalThis,'fetch',async()=>response([event(1,'done',{outcome:'cancelled'})]))
  const events=[];await streamV2(body,e=>events.push(e),new AbortController().signal)
  assert.equal(events[0].outcome,'cancelled')
})

test('v2 API reports backend HTTP detail and refuses empty response streams',async t=>{
  const mock=t.mock.method(globalThis,'fetch',async()=>new Response(JSON.stringify({detail:'fixture unavailable'}),{status:503}))
  await assert.rejects(request('/knowledge-base'),/fixture unavailable/)
  await assert.rejects(streamV2(body,()=>{},new AbortController().signal),/fixture unavailable/)
  mock.mock.restore();t.mock.method(globalThis,'fetch',async()=>new Response(null))
  await assert.rejects(streamV2(body,()=>{},new AbortController().signal),/没有返回/)
})

test('v2 request uses current routes, JSON body and caller cancellation',async t=>{
  let captured
  t.mock.method(globalThis,'fetch',async(url,options)=>{captured={url,options};return new Response('{"ok":true}')})
  const signal=new AbortController().signal
  assert.deepEqual(await request('/knowledge-base','PATCH',{name:'研究资料'},signal),{ok:true})
  assert.equal(captured.url,'/api/v2/knowledge-base');assert.equal(captured.options.method,'PATCH')
  assert.equal(captured.options.signal,signal);assert.deepEqual(JSON.parse(captured.options.body),{name:'研究资料'})
})

test('Markdown supports tables and code, rejects arbitrary HTML and dangerous links',()=>{
  assert.match(markdown.render('| a | b |\n|---|---|\n|1|2|'),/<table>/)
  const html=markdown.render('<script>alert(1)</script>\n\n[bad](javascript:alert(1))')
  assert.doesNotMatch(html,/<script|href="javascript:/)
  assert.match(markdown.render('```text\n<br>\n```'),/&lt;br&gt;/)
})

test('source extraction preserves exact original and ambiguous source text',()=>{
  const original='收入 12.30 亿元。\n\n来源可能不完整，请核对原文。'
  const result=splitAnswerSources(original)
  assert.equal(result.original,original)
  assert.equal(result.body,original)
})
