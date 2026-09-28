import { useEffect, useRef, useState } from 'react'
import { authenticatedFetch } from './cloud-client'
type Report = {
  delivery_state:string; dimensions:{id:string;value:number;tolerance:string;reference:boolean;basis:string;source:{locations?:{page:number;text:string}[]}}[]
  validation:{errors:string[];warnings:string[]}; calculations:{wall_thickness_mm:number|null;allowances:{dimension:string;calculation:string;error:string;warning?:string;basis:string}[]}
  manufacturing_route:string[]; cad_kernel:{status:string;bounding_box_mm?:number[]}
}
const labels:Record<string,string>={outer_diameter_mm:'外径',inner_diameter_mm:'内径',overall_length_mm:'总长',thickness_mm:'厚度'}
function label(x:string){let text=x.replace(/segments\.(\d+)\.(length_mm|diameter_mm)/g,(_,i,k)=>`第 ${Number(i)+1} 段${k==='length_mm'?'长度':'直径'}`).replace(/machining_zones\.(\d+)\.length_mm/g,(_,i)=>`加工区域 ${Number(i)+1} 深度`);for(const [key,value] of Object.entries(labels))text=text.replaceAll(key,value);return text}
export function EngineeringReport({partId,name,onClose}:{partId:string;name:string;onClose:()=>void}) {
  const dialog=useRef<HTMLDialogElement>(null)
  const [data,setData]=useState<Report|null>(null),[error,setError]=useState(''),[attempt,setAttempt]=useState(0)
  useEffect(()=>{const previous=document.activeElement as HTMLElement|null;const overflow=document.body.style.overflow;document.body.style.overflow='hidden';dialog.current?.showModal();return()=>{document.body.style.overflow=overflow;previous?.focus()}},[])
  useEffect(()=>{const c=new AbortController();setData(null);setError('');authenticatedFetch(`/api/parts/${partId}/engineering-model`,{signal:c.signal}).then(async r=>{if(!r.ok)throw new Error(`报告加载失败（${r.status}）`);return r.json()}).then(setData).catch(e=>{if(e.name!=='AbortError')setError(e.message)});return()=>c.abort()},[partId,attempt])
  return <dialog ref={dialog} className="engineering-report" aria-labelledby="report-title" onCancel={onClose} onClick={e=>{if(e.target===dialog.current)onClose()}}>
    <header><div><h2 id="report-title">工程数据与校核报告</h2><p>{name} · 当前已保存参数 · 单位 mm</p></div><button autoFocus onClick={onClose} aria-label="关闭校核报告">关闭 ×</button></header>
    <div className="report-body" aria-busy={!data&&!error}>{error?<div role="alert"><p>{error}</p><button onClick={()=>setAttempt(v=>v+1)}>重试</button></div>:!data?<p role="status">正在计算工程数据与几何校核…</p>:<>
      <section><h3>校核结果 · {data.validation.errors.length?'存在阻塞项':'未发现数值阻塞'}</h3>{data.validation.errors.map((x,i)=><p className="report-error" key={i}>{label(x)}</p>)}<details open={data.validation.warnings.length>0}><summary>待复核提示（{data.validation.warnings.length}）</summary><ul>{data.validation.warnings.map((x,i)=><li key={i}>{label(x)}</li>)}</ul></details><p>交付状态：{data.delivery_state}</p></section>
      <section><h3>尺寸与图纸来源</h3><div className="report-table"><table><thead><tr><th>要素</th><th>尺寸 / 公差</th><th>依据</th></tr></thead><tbody>{data.dimensions.map(d=><tr key={d.id}><td>{label(d.id)}{d.reference?'（参考）':''}</td><td>{d.value} {d.tolerance}</td><td>{d.basis==='user'?'用户保存参数':d.source?.locations?.length?d.source.locations.map((s,i)=><div key={i}>第 {s.page} 页：{s.text}</div>):'尚无定位证据'}</td></tr>)}</tbody></table></div>{!data.dimensions.length&&<p>尚无可校核的尺寸。</p>}</section>
      <section><h3>制造放量</h3>{data.calculations.allowances.length?data.calculations.allowances.map((a,i)=><div className="report-allowance" key={i}><b>{label(a.dimension)}</b><p>{a.calculation||'待确定'} · {a.basis}</p>{(a.error||a.warning)&&<p className={a.error?'report-error':''}>{a.error||a.warning}</p>}</div>):<p>尚无放量方案。</p>}<p>壁厚：{data.calculations.wall_thickness_mm??'不适用'}</p></section>
      <section><h3>核心工艺路线</h3><ol>{data.manufacturing_route.map((x,i)=><li key={i}>{label(x)}</li>)}</ol>{!data.manufacturing_route.length&&<p>尚未形成路线。</p>}</section>
      <section><h3>基础实体检查</h3><p>{({valid:'基础实体有效',invalid:'基础实体无效',unavailable:'几何内核不可用',unsupported:'当前类型尚不支持'} as Record<string,string>)[data.cad_kernel.status]||'尚未检查'}</p>{data.cad_kernel.bounding_box_mm&&<p>包围盒：{data.cad_kernel.bounding_box_mm.map(v=>v.toFixed(2)).join(' × ')} mm</p>}<p>检查仅覆盖主体几何，不代表螺纹、槽、倒角及制造要求已通过审核。</p></section>
    </>}</div>
  </dialog>
}

