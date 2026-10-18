// Real source files, real local retriever and real browser. No generated answers.
import {chromium,expect} from '@playwright/test'
import assert from 'node:assert/strict'
import {createHash} from 'node:crypto'
import {readdir,readFile,writeFile,mkdir} from 'node:fs/promises'
import path from 'node:path'
const base=process.env.VERIFY_BASE_URL||'http://127.0.0.1:5173'
const source=path.resolve('../../dataset/raw_pdf'),out=path.resolve('test-artifacts/single-workspace')
const hash=bytes=>createHash('sha256').update(bytes).digest('hex')
async function sourceFiles(folder,prefix=''){
  const files=[]
  for(const entry of await readdir(folder,{withFileTypes:true})){
    if(entry.isSymbolicLink())continue
    if(entry.isDirectory())files.push(...await sourceFiles(path.join(folder,entry.name),prefix+entry.name+'/'))
    else if(/\.pdf$/i.test(entry.name))files.push({name:prefix+entry.name,sha256:hash(await readFile(path.join(folder,entry.name)))})
  }
  return files
}
await mkdir(out,{recursive:true})
const browser=await chromium.launch({channel:process.env.PLAYWRIGHT_CHANNEL||'msedge'})
try{
  const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage()
  const errors=[],authRequests=[]
  page.on('pageerror',error=>errors.push(error.message))
  page.on('request',request=>{if(/\/api\/v2\/(auth|users|audit|.*\/members)(\/|\?|$)/.test(request.url()))authRequests.push(request.url())})
  async function api(endpoint,options){const response=await context.request.fetch(base+'/api/v2'+endpoint,options);assert(response.ok(),`${endpoint}: ${response.status()} ${await response.text()}`);return response.json()}
  const knowledge=await api('/knowledge-base')
  assert.equal(knowledge.mode,'single');assert.equal(knowledge.processing,false)
  const files=[]
  for(let offset=0;;offset+=100){const result=await api('/knowledge-base/documents?offset='+offset+'&limit=100');files.push(...result.items);if(files.length>=result.total)break}
  const originals=await sourceFiles(source)
  assert.deepEqual(files.map(f=>f.name).sort(),originals.map(f=>f.name).sort())
  assert.equal(knowledge.ready_count,originals.length)
  const retrieval=[]
  for(const original of originals){
    const document=files.find(f=>f.name===original.name)
    assert(document.participates_in_rag&&document.original_available)
    assert.equal(document.doc_version,original.sha256)
    const company=['贵州茅台','海光信息','宁德时代'].find(c=>original.name.includes(c))
    const query=company?`${company}的主营业务与核心技术是什么？`:path.basename(original.name,'.pdf')
    const preview=await api('/search-preview',{method:'POST',data:{query}})
    const hits=preview.candidates.filter(c=>c.doc_id===document.id)
    assert(hits.length>0,'All-library retrieval must find '+original.name)
    assert(hits.every(c=>c.doc_version===original.sha256&&c.index_version===preview.index_version&&c.source_verified))
    const pdf=await context.request.get(base+`/api/v2/documents/${document.id}/pdf?version=${document.doc_version}`)
    assert(pdf.ok());assert.equal(hash(await pdf.body()),original.sha256)
    retrieval.push({name:original.name,doc_id:document.id,doc_version:document.doc_version,index_version:document.index_version,pages:document.pages,query,matching_candidates:hits.length,candidate_domain:preview.candidate_domain})
    console.log(`Verified actual retrieval/PDF: ${original.name} (${document.pages} pages)`)
  }
  await page.goto(base)
  await page.getByRole('button',{name:'知识库',exact:true}).click()
  await expect(page.locator('.document-table tbody tr')).toHaveCount(files.length)
  await page.getByRole('button',{name:'重命名知识库'}).click()
  await page.getByLabel('知识库名称',{exact:true}).fill('研报知识库')
  await page.getByRole('button',{name:'保存名称',exact:true}).click()
  await expect(page.getByRole('heading',{name:'研报知识库',exact:true})).toBeVisible()
  await page.reload();await page.getByRole('button',{name:'知识库',exact:true}).click()
  await expect(page.getByRole('heading',{name:'研报知识库',exact:true})).toBeVisible()
  for(const width of [1440,390]){
    await page.setViewportSize({width,height:1000})
    for(const theme of ['light','dark']){
      await page.getByLabel('外观主题').selectOption(theme)
      await expect(page.locator('.document-table tbody tr')).toHaveCount(files.length)
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'page overflow')
      await page.screenshot({path:path.join(out,`catalog-${width}-${theme}.png`)})
    }
  }
  // Check a preserved real conversation and its original citation PDF.
  let historicalPdf=false
  for(const session of await api('/sessions')){
    const detail=await api('/sessions/'+session.id)
    if(!detail.messages.some(m=>m.role==='assistant'&&m.active&&m.status==='complete'&&!m.unavailable&&m.citations?.length))continue
    await page.evaluate(id=>localStorage.setItem('research-current-session',id),session.id)
    await page.reload()
    const cite=page.getByRole('button',{name:/^查看引用 \d+$/}).last()
    await expect(cite).toBeVisible();await cite.click()
    await page.locator('.selected-citation').getByRole('button',{name:/查看原文/}).click()
    await expect(page.locator('canvas[data-rendered-page]')).toBeVisible({timeout:25000})
    await page.screenshot({path:path.join(out,'preserved-history-pdf.png')})
    historicalPdf=true;break
  }
  assert.deepEqual(errors,[]);assert.deepEqual(authRequests,[])
  const result={checked_at:new Date().toISOString(),knowledge_base:await api('/knowledge-base'),originals,retrieval,historical_pdf_verified:historicalPdf,
    historical_pdf_note:historicalPdf?'Preserved conversation citation opened successfully':'No currently available cited historical answer; same-version original PDF verified for every current file above',
    checks:['All source PDFs participate; no file-selection subset','Per-file real unfiltered retrieval and same-version original PDF SHA256','Single KB rename survives reload','1440/390px light/dark layouts without overflow','No authentication or membership requests; zero browser errors'],paid_generation_requests:0}
  await writeFile(path.join(out,'results.json'),JSON.stringify(result,null,2))
  console.log(`Single KB verified: ${files.length} real PDFs; rename, 4 layouts and historical PDF=${historicalPdf}`)
}finally{await browser.close()}
