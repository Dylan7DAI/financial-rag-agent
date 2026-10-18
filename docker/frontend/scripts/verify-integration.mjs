// Real shared Agent graph, tools, generation, citations and PDF. Uses model API.
import {chromium} from '@playwright/test'
import assert from 'node:assert/strict'
import {randomUUID} from 'node:crypto'
import {mkdir,readFile,writeFile} from 'node:fs/promises'
import path from 'node:path'
const base=process.env.VERIFY_BASE_URL||'http://127.0.0.1:5173',out=path.resolve('test-artifacts/integration')
await mkdir(out,{recursive:true})
const resultFile=path.join(out,'results.json'),browser=await chromium.launch({channel:process.env.PLAYWRIGHT_CHANNEL||'msedge'})
let result={started_at:new Date().toISOString(),kind:'REAL_AGENT_REAL_MODELS_REAL_PDFS',answers:[],checks:[]}
try{
  const context=await browser.newContext()
  async function api(endpoint,options){
    const r=await context.request.fetch(base+'/api/v2'+endpoint,{timeout:240000,...options})
    assert(r.ok(),`${endpoint}: ${r.status()} ${await r.text()}`);return r
  }
  if(process.env.INTEGRATION_RESUME==='1')result=JSON.parse(await readFile(resultFile,'utf8'))
  if(!result.session_id){
    const session=await (await api('/sessions',{method:'POST'})).json();result.session_id=session.id
    await api('/sessions/'+session.id,{method:'PATCH',data:{title:'新旧整合验收（可删除）'}})
    await writeFile(resultFile,JSON.stringify(result,null,2))
  }
  const tools=await (await api('/tools')).json()
  assert.equal(tools.length,4)
  const catalog=await (await api('/tools/report_catalog',{method:'POST',data:{arguments:{limit:100}}})).json()
  const files=(await (await api('/knowledge-base/documents?limit=100')).json()).items
  assert.equal(catalog.result.reports.length,files.length)
  const calc=await (await api('/tools/metric_calculate',{method:'POST',data:{arguments:{operation:'growth_rate',values:[125,100]}}})).json()
  assert.equal(calc.result.result,25)
  const preview=await (await api('/tools/report_search',{method:'POST',data:{arguments:{query:'海光信息2021年营业收入'}}})).json()
  assert(preview.result.docs.length)
  const chunk=preview.result.docs[0]
  const expanded=await (await api('/tools/context_expand',{method:'POST',data:{arguments:{doc_id:chunk.doc_id,chunk_id:chunk.chunk_id}}})).json()
  assert(expanded.result.docs.some(d=>d.chunk_id===chunk.chunk_id))
  result.checks.push('Four real tools callable on the current single-library snapshot')
  const questions=[
    '海光信息2022年营业收入和归母净利润分别是多少？请保留单位。',
    '那2021年的营业收入呢？',
    '比较海光信息与宁德时代的主营业务，各用一句话说明并引用原文。',
  ]
  for(const [index,query] of questions.entries()){
    if(result.answers[index]?.outcome==='answered')continue
    const previous=result.answers[index]
    if(previous){result.failed_attempts ||= [];result.failed_attempts.push(previous)}
    const response=await api('/chat',{method:'POST',data:{session_id:result.session_id,request_id:randomUUID(),
      ...(previous?{regenerate_message_id:previous.message_id}:{query})}})
    const events=(await response.text()).split(/\r?\n/).filter(line=>line.startsWith('data: ')).map(line=>JSON.parse(line.slice(6)))
    const final=events.find(e=>e.type==='final')
    assert(final,JSON.stringify(events.at(-1)));result.answers[index]={query,...final}
    await writeFile(resultFile,JSON.stringify(result,null,2))
    assert.equal(final.outcome,'answered',final.content)
    assert.equal(final.citation_integrity,'passed');assert(final.citations.length)
    assert(final.observations.some(o=>o.tool==='report_search'&&o.status==='success'))
    for(const c of final.citations){
      const file=files.find(f=>f.id===c.doc_id);assert(file);assert.equal(c.doc_version,file.doc_version)
      assert(c.source_verified&&c.included_in_prompt)
      assert((await api(`/documents/${c.doc_id}/pdf?version=${c.doc_version}&page=${c.page}`)).ok())
    }
    if(index===0)assert(/51[.,]?25|5[,.]?125/.test(final.content)&&/8[.,]?04|803[.,]?54/.test(final.content),final.content)
    if(index===1){
      assert(/23[.,]?10|2[,.]?310/.test(final.content),final.content)
      assert(!final.observations.some(o=>o.tool==='llm.query_rewrite'),'Short follow-up should use rule rewrite')
    }
    if(index===2){
      assert(final.observations.some(o=>o.tool==='llm.query_rewrite'&&o.status==='success'))
      assert.equal(new Set(final.citations.map(c=>c.doc_id)).size,2,'Comparison needs sources from both reports')
    }
    console.log(`Real shared-graph answer ${index+1} verified: ${final.citations.length} citations; first text ${final.usage.first_token_ms} ms`)
  }
  const page=await context.newPage(),errors=[]
  page.on('pageerror',e=>errors.push(e.message))
  await page.addInitScript(id=>localStorage.setItem('research-current-session',id),result.session_id)
  await page.goto(base)
  const {expect}=await import('@playwright/test')
  await expect(page.locator('.message-assistant')).toHaveCount(3)
  await page.getByRole('button',{name:/^查看引用 \d+$/}).last().click()
  await page.locator('.selected-citation').getByRole('button',{name:/查看原文/}).click()
  await expect(page.locator('canvas[data-rendered-page]')).toBeVisible({timeout:25000})
  await page.screenshot({path:path.join(out,'shared-agent-pdf.png')})
  assert.deepEqual(errors,[])
  result.checks.push('Real 2022 facts, rule-based 2021 follow-up, complex rewrite with citations from both companies','Production UI restored all answers and rendered the real cited PDF')
  result.checks=[...new Set(result.checks)];result.finished_at=new Date().toISOString()
  await writeFile(resultFile,JSON.stringify(result,null,2));console.log('Shared Agent integration verified; session '+result.session_id)
}catch(error){result.error=error.message;await writeFile(resultFile,JSON.stringify(result,null,2));throw error}
finally{await browser.close()}
