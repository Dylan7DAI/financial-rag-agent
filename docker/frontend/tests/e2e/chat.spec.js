import { test, expect } from '@playwright/test'
import path from 'node:path'
import { mkdir } from 'node:fs/promises'
test.beforeEach(async ({context}, info)=>{await context.setExtraHTTPHeaders({'x-ui-fixture':info.testId})})
async function send(page,query){await page.getByLabel('输入问题',{exact:true}).fill(query);await page.getByRole('button',{name:'发送问题',exact:true}).click();await expect(page.getByRole('button',{name:'停止生成'})).toHaveCount(0);await expect(page.locator('.message-assistant')).not.toHaveCount(0)}
async function overflow(page){expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);expect(await page.locator('.conversation-scroll').evaluate(el=>el.scrollWidth<=el.clientWidth)).toBe(true)}
async function shot(page,name){await mkdir('test-artifacts/p1',{recursive:true});await page.screenshot({path:path.resolve('test-artifacts/p1',name)})}

for(const width of [1440,1024,390])test(`${width}px verified reference -> source -> PDF physical page in two clicks`,async({page})=>{
  const errors=[];page.on('pageerror',e=>errors.push(e.message))
  await page.setViewportSize({width,height:width===390?844:1000});await page.goto('/')
  await send(page,'fixture 公司2024营收');await overflow(page)
  await expect(page.locator('.message-assistant table')).toHaveCount(1)
  await page.getByRole('button',{name:'查看引用 1',exact:true}).click()
  await expect(page.locator('.selected-citation')).toBeVisible()
  await expect(page.locator('.evidence-meta')).toContainText('PDF 物理第 1 页')
  await page.getByRole('button',{name:'查看原文 · 第 1 页',exact:true}).click()
  await expect(page.locator('canvas[data-rendered-page="1"]')).toBeVisible({timeout:15000})
  await expect(page.getByLabel('PDF物理页码')).toHaveValue('1')
  await shot(page,`p1-pdf-${width}.png`)
  await page.getByRole('button',{name:'下一页',exact:true}).click();await expect(page.locator('canvas[data-rendered-page="2"]')).toBeVisible()
  await page.getByRole('button',{name:'关闭PDF原文'}).click();await page.getByRole('button',{name:'关闭来源面板'}).click()
  await send(page,'追问2023年呢');await expect(page.locator('.message-user')).toHaveCount(2)
  await page.getByRole('button',{name:'查看引用 1',exact:true}).first().click();await expect(page.locator('.evidence-meta')).toContainText('PDF 物理第 1 页')
  await page.getByRole('button',{name:'关闭来源面板'}).click();await page.getByRole('button',{name:'查看引用 1',exact:true}).last().click();await expect(page.locator('.evidence-meta')).toContainText('PDF 物理第 2 页')
  await shot(page,`p1-citations-${width}.png`);await overflow(page);expect(errors).toEqual([])
})

test('draft is provisional; repair replaces it atomically and reload restores final',async({page})=>{
  await page.goto('/');await send(page,'修复 fixture');await expect(page.locator('.message-assistant')).not.toContainText('错误草稿')
  await expect(page.getByRole('button',{name:'查看引用 1',exact:true})).toBeVisible();await page.reload()
  await expect(page.locator('.message-user')).toHaveCount(1);await expect(page.getByRole('button',{name:'查看引用 1',exact:true})).toBeVisible()
})

test('stop cancels server request and discards late text',async({page})=>{
  await page.goto('/');await page.getByLabel('输入问题',{exact:true}).fill('慢速 fixture');await page.getByRole('button',{name:'发送问题',exact:true}).click()
  await expect(page.getByText('草稿 · 引用待校验')).toBeVisible();await page.getByRole('button',{name:'停止生成'}).click()
  await expect(page.getByText('已停止。此草稿不会用于后续上下文；底层工作可能尚在退出。')).toBeVisible()
  await expect(page.getByRole('button',{name:'查看引用 1',exact:true})).toHaveCount(0)
  await page.reload();await expect(page.getByText(/已停止。此草稿/)).toBeVisible();await send(page,'恢复提问')
  await expect(page.locator('.message-user')).toHaveCount(2)
})

test('regenerate keeps one user question, old version, feedback and copy persist',async({page,context})=>{
  await context.grantPermissions(['clipboard-read','clipboard-write'])
  await page.goto('/');await send(page,'版本 fixture');await page.getByRole('button',{name:'重新生成',exact:true}).click()
  await expect(page.getByRole('button',{name:'停止生成'})).toHaveCount(0)
  await expect(page.locator('.message-user')).toHaveCount(1);await expect(page.getByText('历史回答版本 · 1')).toBeVisible()
  await page.getByRole('button',{name:'有帮助',exact:true}).click();await expect(page.getByRole('button',{name:'有帮助',exact:true})).toHaveAttribute('aria-pressed','true')
  await page.getByRole('button',{name:'复制',exact:true}).click();await expect(page.getByRole('status')).toContainText('已复制')
  await page.reload();await expect(page.getByRole('button',{name:'有帮助',exact:true})).toHaveAttribute('aria-pressed','true')
})

test('rename search delete and clear confirmation with failed clear preservation',async({page})=>{
  await page.goto('/');await send(page,'清空失败 fixture')
  await page.getByRole('button',{name:'清空',exact:true}).click();await page.getByRole('button',{name:'确认',exact:true}).click()
  await expect(page.getByRole('alert')).toContainText('现有内容已保留');await expect(page.locator('.message-user')).toHaveCount(1)
  await page.getByRole('button',{name:'确认',exact:true}).click();await expect(page.getByRole('dialog')).toHaveCount(0);await expect(page.locator('.message-user')).toHaveCount(0)
  await page.getByRole('button',{name:'重命名',exact:true}).click();await page.getByRole('dialog').locator('input').fill('持久化会话');await page.getByRole('button',{name:'确认',exact:true}).click()
  await expect(page.getByRole('heading',{name:'持久化会话',exact:true})).toBeVisible();await page.reload();await expect(page.getByRole('heading',{name:'持久化会话',exact:true})).toBeVisible()
  await page.getByLabel('搜索会话').fill('不存在');await expect(page.locator('.session-item')).toHaveCount(0)
  await page.getByLabel('搜索会话').fill('持久化');await expect(page.locator('.session-item')).toHaveCount(1)
  await page.getByRole('button',{name:'删除',exact:true}).click();await page.getByRole('button',{name:'确认',exact:true}).click();await expect(page.getByRole('heading',{name:'新会话',exact:true})).toBeVisible()
})

test('disconnect and error do not become completed answers; PDF errors are clear',async({page})=>{
  await page.goto('/');await send(page,'断流 fixture');await expect(page.getByRole('alert')).toContainText('连接提前结束')
  await send(page,'错误 fixture');await expect(page.getByRole('alert').last()).toContainText('fixture 模型失败')
  await send(page,'原文失败 fixture');await page.getByRole('button',{name:'查看引用 1',exact:true}).click();await page.getByRole('button',{name:'查看原文 · 第 1 页'}).click();await expect(page.getByRole('dialog').getByRole('alert')).toContainText('原文版本已变化')
})

test('IME, blank and duplicate send guards; Shift+Enter makes a new line',async({page})=>{
  await page.goto('/');const input=page.getByLabel('输入问题',{exact:true});await input.fill('   ');await expect(page.getByRole('button',{name:'发送问题'})).toBeDisabled()
  await input.fill('中文');await input.dispatchEvent('compositionstart');await input.dispatchEvent('keydown',{key:'Enter',isComposing:true});await expect(page.locator('.message-user')).toHaveCount(0)
  await input.dispatchEvent('compositionend');await input.press('Shift+Enter');await expect(input).toHaveValue('中文\n')
  await input.press('Enter');await input.fill('重复问题');await input.press('Enter');await expect(page.getByRole('button',{name:'停止生成'})).toHaveCount(0)
  await expect(page.locator('.message-user')).toHaveCount(1);await expect(input).toHaveValue('重复问题')
})

test('in-flight replies stay in their session while another session is used',async({page})=>{
  await page.goto('/');await page.getByLabel('输入问题',{exact:true}).fill('慢速 会话甲');await page.getByRole('button',{name:'发送问题'}).click()
  await page.getByRole('button',{name:'新建会话',exact:true}).click();await expect(page.locator('.message-assistant')).toHaveCount(0)
  await send(page,'会话乙问题');await expect(page.locator('.message-user')).toHaveCount(1)
  await page.getByRole('button',{name:'慢速 会话甲',exact:true}).click();await expect(page.getByRole('button',{name:'停止生成'})).toHaveCount(0)
  await expect(page.locator('.message-user')).toHaveCount(1);await expect(page.locator('.message-assistant')).toHaveCount(1)
  await expect(page.locator('.user-bubble')).toHaveText('慢速 会话甲')
})

test('no evidence has no verified citation links',async({page})=>{
  await page.goto('/');await send(page,'无依据 fixture');await expect(page.getByText('当前材料不足 · 未提供已核验引用')).toBeVisible()
  await expect(page.getByRole('button',{name:/^查看引用 \d+$/})).toHaveCount(0)
})

test('HTTP failure retains the question and retry does not duplicate it',async({page})=>{
  await page.goto('/');await send(page,'HTTP失败 fixture');await expect(page.locator('.user-bubble')).toHaveText('HTTP失败 fixture')
  await expect(page.getByRole('alert')).toContainText('fixture HTTP服务暂不可用')
  await page.getByRole('button',{name:'重试',exact:true}).click();await expect(page.getByRole('button',{name:'停止生成'})).toHaveCount(0)
  await expect(page.locator('.message-user')).toHaveCount(1);await expect(page.getByRole('button',{name:'查看引用 1',exact:true})).toBeVisible()
})

test('disconnect before first status recovers accepted message ID before retry',async({page})=>{
  await page.goto('/');await send(page,'确认前中断 fixture');await expect(page.getByRole('alert')).toContainText('确认前连接中断')
  await expect(page.locator('.message-user')).toHaveCount(1)
  await page.getByRole('button',{name:'重试',exact:true}).click();await expect(page.getByRole('button',{name:'停止生成'})).toHaveCount(0)
  await expect(page.locator('.message-user')).toHaveCount(1);await expect(page.getByText('历史回答版本 · 1')).toBeVisible()
})
