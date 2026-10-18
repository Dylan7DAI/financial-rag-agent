import { test, expect } from '@playwright/test'
test('real coordinate contract transforms rotation/zoom and rejects bad/whole-page boxes', async ({page}) => {
  await page.goto('/')
  const result=await page.evaluate(async()=>{
    const {viewportRegion}=await import('/src/lib/pdfRegions.ts')
    const m=[1,0,0,-1,0,600]
    return [viewportRegion([40,50,150,90],m,(x,y)=>[x,600-y],500,600),
      viewportRegion([40,50,150,90],m,(x,y)=>[2*(600-y),2*x],1200,1000),
      viewportRegion([-10,0,150,90],m,(x,y)=>[x,600-y],500,600),
      viewportRegion([0,0,500,600],m,(x,y)=>[x,600-y],500,600),
      viewportRegion([40,50,150,90],[0,0,0,0,0,0],(x,y)=>[x,y],500,600),
      viewportRegion([40,50,150,90],[1,0,0,-1,-50,700],(x,y)=>[x-50,700-y],500,600),
      viewportRegion([180,150,250,190],m,(x,y)=>[x,600-y],500,600)]
  })
  expect(result).toEqual([[40,50,150,90],[100,80,180,300],null,null,null,[40,50,150,90],[180,150,250,190]])
})
