import { useEffect, useRef, useState } from 'react'
import './cad-editor.css'
export function CadEditor({partId,onClose,onSaved}:{partId:string;onClose:()=>void;onSaved:()=>void}) {
  const frame=useRef<HTMLIFrameElement>(null)
  const [ready,setReady]=useState(false),[slow,setSlow]=useState(false),[attempt,setAttempt]=useState(0)
  useEffect(()=>{setReady(false);setSlow(false);const timer=window.setTimeout(()=>setSlow(true),20000);return()=>clearTimeout(timer)},[partId,attempt])
  useEffect(()=>{
    const receive=(event:MessageEvent)=>{
      if(event.origin!==location.origin || event.source!==frame.current?.contentWindow || event.data?.partId!==partId)return
      if(event.data.type==='cad-studio-saved')onSaved()
      if(event.data.type==='cad-studio-ready')setReady(true)
      if(event.data.type==='cad-studio-close')onClose()
    }
    window.addEventListener('message',receive)
    return()=>window.removeEventListener('message',receive)
  },[partId,onClose,onSaved])
  return <div className="cad-studio-modal" role="dialog" aria-modal="true" aria-label="工程 CAD Studio">
    {!ready && <div className="cad-startup" role="status"><strong>{slow?'CAD 编辑器启动时间较长':'正在启动 CAD Studio'}</strong><span>{slow?'请重试；如果仍无法打开，可关闭后继续查看图纸。':'首次加载包含绘图引擎和中文字体，请稍候。'}</span>{slow&&<button onClick={()=>setAttempt(v=>v+1)}>重新加载</button>}<button onClick={onClose}>关闭</button></div>}
    <iframe key={attempt} ref={frame} title="MLightCAD 在线编辑器" src={`/cad-studio.html?part=${encodeURIComponent(partId)}`}/>
  </div>
}
