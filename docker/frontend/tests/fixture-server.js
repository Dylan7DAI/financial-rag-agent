// Explicit v2 UI fixture, isolated by x-ui-fixture header per browser context.
import http from 'node:http'
import { randomUUID } from 'node:crypto'
const states = new Map(), jobs = new Map()
const baseNames = new Map()
const stamp = () => new Date().toISOString()
const longName = 'UI_FIXTURE_' + '长文件名用于布局验证'.repeat(8) + '.pdf'
const citation = page => ({ id: 1, doc_id: 'fixture-document', doc_version: 'fixture-version', index_version: 'fixture-index',
  doc_name: longName, page, page_label: String(page), chunk_id: 'fixture-' + page, snippet: '**UI fixture 原始片段**\n\n收入 100.00 亿元。',
  original_length: 32, truncated: false, bbox: null, source_verified: true, included_in_prompt: true, score: 0 })

function pdf() {
  const objects = ['<< /Type /Catalog /Pages 2 0 R >>', '<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 500] /Resources << /Font << /F1 7 0 R >> >> /Contents 4 0 R >>',
    '<< /Length 53 >>\nstream\nBT /F1 18 Tf 40 430 Td (UI FIXTURE PAGE ONE) Tj ET\nendstream',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 500] /Resources << /Font << /F1 7 0 R >> >> /Contents 6 0 R >>',
    '<< /Length 53 >>\nstream\nBT /F1 18 Tf 40 430 Td (UI FIXTURE PAGE TWO) Tj ET\nendstream',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
  let output = '%PDF-1.4\n', offsets = [0]
  objects.forEach((object, index) => { offsets.push(Buffer.byteLength(output)); output += `${index+1} 0 obj\n${object}\nendobj\n` })
  const xref = Buffer.byteLength(output)
  output += `xref\n0 ${objects.length+1}\n0000000000 65535 f \n` + offsets.slice(1).map(n => String(n).padStart(10,'0')+' 00000 n \n').join('')
  return Buffer.from(output + `trailer\n<< /Size ${objects.length+1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`)
}
const file = pdf()
const json = (response, body, code = 200) => { response.writeHead(code, { 'Content-Type': 'application/json' }); response.end(JSON.stringify(body)) }
const wait = delay => new Promise(resolve => setTimeout(resolve,delay))
http.createServer(async (request,response) => {
  if (request.url === '/health') { response.end('Explicit P1 UI fixture'); return }
  const owner = request.headers['x-ui-fixture'] || 'default'
  if (!states.has(owner)) states.set(owner,new Map())
  const sessions = states.get(owner)
  const url = new URL(request.url,'http://fixture'), path = url.pathname.replace('/api/v2','')
  let body = ''; for await (const chunk of request) body += chunk
  const data = body ? JSON.parse(body) : {}
  if(path==='/knowledge-base'){
    if(request.method==='PATCH'){
      if(typeof data.name!=='string'||!data.name.trim()){json(response,{detail:'名称不能为空'},422);return}
      baseNames.set(owner,data.name.trim())
    }
    json(response,{id:'UI_FIXTURE_KB',name:baseNames.get(owner)||'UI_FIXTURE 知识库',document_count:1,ready_count:1,processing:false,mode:'single'});return
  }
  if(path==='/knowledge-base/documents'){json(response,{total:1,items:[{id:'fixture-document',name:longName,pages:2,doc_version:'fixture-version',index_version:'fixture-index',state:'ready',error:null,original_available:true,participates_in_rag:true}]});return}
  if (path === '/sessions') {
    if (request.method === 'POST') {
      const s = { id:randomUUID(),title:'新会话',owner_id:'local',workspace_id:'local',created_at:stamp(),updated_at:stamp(),messages:[] }
      sessions.set(s.id,s); json(response,s)
    } else json(response,[...sessions.values()].filter(s=>!url.searchParams.get('q') || (s.title + s.messages.map(m=>m.query || m.content).join()).includes(url.searchParams.get('q'))).map(({messages,...s})=>s))
    return
  }
  const match = path.match(/^\/sessions\/([^/]+)(\/clear)?$/)
  if (match) {
    const s = sessions.get(match[1]); if (!s) { json(response,{detail:'会话不存在'},404); return }
    if (request.method === 'DELETE') { sessions.delete(s.id); json(response,{status:'success'}) }
    else if (request.method === 'PATCH') { s.title = data.title; json(response,s) }
    else if (match[2]) {
      if (s.title.includes('清空失败')) { s.title = '可重试'; json(response,{detail:'fixture 清空失败'},500) }
      else { s.messages=[]; s.title='新会话'; json(response,{status:'success'}) }
    } else json(response,s)
    return
  }
  const cancel = path.match(/^\/requests\/([^/]+)\/cancel$/)
  const lookup = path.match(/^\/requests\/([^/]+)$/)
  if (lookup) {
    const message=[...sessions.values()].flatMap(s=>s.messages).find(m=>m.request_id===lookup[1])
    if(message)json(response,{session_id:message.session_id,message});else json(response,{detail:'请求不存在'},404)
    return
  }
  if (cancel) {
    const job = jobs.get(cancel[1]); if (job) job.cancel()
    json(response,{status:'cancelled'}); return
  }
  const operation = path.match(/^\/messages\/([^/]+)\/(copy|feedback)$/)
  if (operation) {
    const message = [...sessions.values()].flatMap(s=>s.messages).find(m=>m.id===operation[1])
    if (!message) { json(response,{detail:'消息不存在'},404); return }
    if (operation[2]==='feedback') { Object.assign(message,data); json(response,message) }
    else { message.copy_count=(message.copy_count||0)+1; json(response,{status:'success'}) }
    return
  }
  if (path.startsWith('/documents/')) {
    if (path.includes('unavailable')) { json(response,{detail:'原文版本已变化'},409); return }
    response.writeHead(200,{'Content-Type':'application/pdf'}); response.end(file); return
  }
  if (path !== '/chat') { json(response,{detail:'fixture unknown path'},404); return }
  const session = sessions.get(data.session_id)
  if (!session) { json(response,{detail:'会话不存在'},404); return }
  if(data.query?.includes('HTTP失败') && !session.httpFailed){session.httpFailed=true;json(response,{detail:'fixture HTTP服务暂不可用'},503);return}
  if (session.messages.some(m=>m.status==='pending')) { json(response,{detail:'此会话正在回答'},409); return }
  let user, previous
  if (data.regenerate_message_id) {
    previous=session.messages.find(m=>m.id===data.regenerate_message_id)
    user=session.messages.find(m=>m.id===previous.parent_id); previous.active=false
  } else {
    user={id:randomUUID(),session_id:session.id,role:'user',content:data.query,status:'complete',active:true,citations:[],candidates:[],created_at:stamp()}
    session.messages.push(user); if(session.title==='新会话')session.title=data.query.slice(0,32)
  }
  const message={id:randomUUID(),session_id:session.id,role:'assistant',query:user.content,content:'',status:'pending',active:true,
    request_id:data.request_id,parent_id:user.id,replaces_id:previous?.id,citations:[],candidates:[],vote:0,created_at:stamp()}
  session.messages.push(message)
  response.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache'})
  let seq=0,ended=false
  const emit=(type,extra={})=> {if(!ended)response.write(`data: ${JSON.stringify({protocol:2,request_id:data.request_id,session_id:session.id,message_id:message.id,seq:++seq,type,...extra})}\r\n\r\n`)}
  const job={cancel:()=>{if(ended)return;message.status='cancelled';message.outcome='cancelled';emit('done',{outcome:'cancelled'});ended=true;response.end()}}
  jobs.set(data.request_id,job)
  response.on('close',()=>{if(!ended){message.status='cancelled';ended=true}jobs.delete(data.request_id)})
  if(user.content.includes('确认前中断') && !session.unconfirmedFailed){session.unconfirmedFailed=true;message.status='error';message.error='确认前连接中断';ended=true;response.end();return}
  emit('status',{phase:'retrieving',user_message:user,assistant_message:message})
  await wait(25)
  if(ended)return
  const page=user.content.includes('追问')?2:1
  const c=citation(page);if(user.content.includes('原文失败'))c.doc_id='unavailable'
  message.candidates=[c];emit('citations',{citations:[],candidates:[c]})
  emit('status',{phase:'generating'})
  if(user.content.includes('错误')){message.status='error';message.error='fixture 模型失败';emit('error',{message:message.error,outcome:'error'});ended=true;response.end();return}
  if(user.content.includes('断流')){emit('delta',{text:'未完成草稿'});message.status='error';ended=true;response.end();return}
  const table='| 指标 | 2024 | 2023 | 单位 | 口径 | 备注 |\n| --- | --- | --- | --- | --- | --- |\n| 收入 | 100.00 | 80.00 | 亿元 | 合并报表 | fixture |'
  const answer=`**UI fixture 模拟回答**：收入为100.00亿元[1]。\n\n${table}\n\n\`\`\`text\n${'long-code-'.repeat(45)}\n\`\`\``
  emit('delta',{text:user.content.includes('修复')?'错误草稿[99]':answer.slice(0,14)})
  await wait(user.content.includes('慢速')?1500:80)
  if(ended)return
  if(user.content.includes('修复')){emit('status',{phase:'repairing',reset_draft:true});emit('delta',{text:answer})}
  else emit('delta',{text:answer.slice(14)})
  await wait(20);if(ended)return
  const noEvidence=user.content.includes('无依据')
  Object.assign(message,{content:noEvidence?'当前材料不足。':answer,status:'complete',outcome:noEvidence?'no_evidence':'answered',citations:noEvidence?[]:[c],
    citation_integrity:noEvidence?'not_applicable':'passed',
    usage:{retrieval_ms:25,first_token_ms:28,generation_ms:100,input_tokens:null,output_tokens:null}})
  emit('final',message);emit('done',{outcome:message.outcome});ended=true;response.end()
}).listen(8014,'127.0.0.1')
