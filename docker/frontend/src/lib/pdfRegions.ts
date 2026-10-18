/** Parser coordinates are PyMuPDF unrotated points. Invert its PDF->page
 * matrix, then let PDF.js apply crop/rotation/zoom through its viewport. */
export function pdfPoint(x:number,y:number,m:number[]): [number,number] | null {
  if(m.length!==6 || !m.every(Number.isFinite))return null
  const [a,b,c,d,e,f]=m as [number,number,number,number,number,number],det=a*d-b*c
  if(!det)return null
  return [(d*(x-e)-c*(y-f))/det,(-b*(x-e)+a*(y-f))/det]
}
export function viewportRegion(box:number[],matrix:number[],convert:(x:number,y:number)=>number[],width:number,height:number): number[] | null {
  if(box.length!==4 || !box.every(Number.isFinite) || box[0]!>=box[2]! || box[1]!>=box[3]!)return null
  const points=[[box[0]!,box[1]!],[box[2]!,box[1]!],[box[0]!,box[3]!],[box[2]!,box[3]!]].map(([x,y])=>pdfPoint(x!,y!,matrix)).map(p=>p?convert(...p):null)
  if(points.some(p=>!p || !p.every(Number.isFinite)))return null
  const xs=points.map(p=>p![0]!),ys=points.map(p=>p![1]!),out=[Math.min(...xs),Math.min(...ys),Math.max(...xs),Math.max(...ys)]
  if(out[0]!<-.5 || out[1]!<-.5 || out[2]!>width+.5 || out[3]!>height+.5)return null
  if((out[2]!-out[0]!)*(out[3]!-out[1]!)>=width*height*.95)return null
  return out
}
