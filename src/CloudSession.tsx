import { useEffect, useState, type ReactNode } from 'react'
import { waitTask } from './cloud-client'

let sessionRequest:Promise<void>|null=null
function openSession() {
  if(!sessionRequest) sessionRequest=(async()=>{
    const r=await fetch('/api/auth/anonymous',{method:'POST'})
    const data=await r.json()
    if(!r.ok)throw new Error(data.detail||'无法建立免账密会话')
    const previous=localStorage.getItem('engineering-owner')
    if(previous && previous!==data.id)localStorage.removeItem('engineering-active-task')
    localStorage.setItem('engineering-owner',data.id)
  })().finally(()=>{sessionRequest=null})
  return sessionRequest
}

export function CloudSession({children}:{children:ReactNode}) {
  const [mode,setMode]=useState<'loading'|'local'|'ready'|'error'>('loading')
  const [error,setError]=useState('')
  const [pending,setPending]=useState('')
  useEffect(()=>{
    let active=true
    fetch('/api/deployment').then(async r=>{
      const config=await r.json().catch(()=>null)
      if(!r.ok)throw new Error(config?.detail || `工程后端连接失败（HTTP ${r.status}），请检查部署地址和 API 路由`)
      if(!config || typeof config.authentication !== 'boolean')throw new Error('工程后端返回格式异常，请检查服务地址和部署保护配置')
      return config
    }).then(async config=>{
      if (!active) return
      if (!config.authentication) {setMode('local');return}
      await openSession()
      if(active)setMode('ready')
    }).catch(e=>{if(active){setError(e.message);setMode('error')}})
    const expired=()=>{setMode('error');setError('会话需要恢复，请重新连接；云端任务会继续执行')}
    window.addEventListener('engineering-session-expired',expired)
    return()=>{active=false;window.removeEventListener('engineering-session-expired',expired)}
  },[])
  useEffect(()=>{
    if(mode!=='ready')return
    const task=localStorage.getItem('engineering-active-task')
    if(!task)return
    setPending('正在恢复上次云端任务…')
    waitTask(task).then(()=>{setPending('任务已完成，刷新工作区可查看最新成果')}).catch(e=>setPending(e.message))
  },[mode])
  if(mode==='local')return <>{children}</>
  if(mode==='ready')return <><div className="cloud-account"><span title="工作区绑定当前浏览器。清除 Cookie 或更换设备后无法自动恢复，请及时导出成果。">{pending||'免账密工作区 · 当前浏览器独立保存'}</span><button onClick={()=>location.reload()}>刷新</button></div>{children}</>
  return <div className="cloud-login"><section><h1>工程解析平台</h1><p>免账号密码工程工作区</p>
    {mode==='loading'?<p>正在恢复工作区…</p>:<><p role="alert">{error}</p><button type="button" onClick={()=>location.reload()}>重新连接</button></>}
  </section></div>
}
