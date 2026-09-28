import { useEffect, useState } from 'react'
export function DrawingPreview({partId,name,revision,approved}:{partId:string;name:string;revision:number;approved:boolean}) {
  const [zoom,setZoom]=useState(100),[failed,setFailed]=useState(false),[retry,setRetry]=useState(0)
  useEffect(()=>{setZoom(100);setFailed(false)},[partId,revision])
  return <div className="drawing-review">
    <div className="drawing-review-tools"><span>{approved?'已审核图纸':'工程草案 · 待审核'}</span><button aria-label="缩小图纸" disabled={zoom<=50} onClick={()=>setZoom(v=>Math.max(50,v-25))}>−</button><b>{zoom}%</b><button aria-label="放大图纸" disabled={zoom>=300} onClick={()=>setZoom(v=>Math.min(300,v+25))}>＋</button><button onClick={()=>setZoom(100)}>适应宽度</button><a href={`/api/parts/${partId}/files/pdf`} target="_blank" rel="noreferrer">打开 PDF</a></div>
    <div className={`drawing-review-scroll ${zoom===100?'fit':''}`}>
      {failed?<div className="preview-empty"><h3>图纸预览加载失败</h3><p>可以重新加载预览，或打开 PDF 查看已保存的图纸。</p><button onClick={()=>{setFailed(false);setRetry(v=>v+1)}}>重新加载</button></div>:<img src={`/api/parts/${partId}/preview.svg?v=${revision}&retry=${retry}`} alt={`${name}制造图，单位毫米`} style={zoom===100?undefined:{width:`${zoom}%`}} onError={()=>setFailed(true)}/>}
    </div>
  </div>
}
