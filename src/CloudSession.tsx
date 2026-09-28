import { useEffect, useState, type ReactNode } from 'react'
import { authenticatedFetch, waitTask } from './cloud-client'

export function CloudSession({children}:{children:ReactNode}) {
  const [mode,setMode]=useState<'loading'|'local'|'login'|'ready'|'error'>('loading')
  const [email,setEmail]=useState(''),[password,setPassword]=useState(''),[error,setError]=useState('')
  const [busy,setBusy]=useState(false),[pending,setPending]=useState('')
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
      const r=await authenticatedFetch('/api/auth/me')
      if(active)setMode(r.ok?'ready':'login')
    }).catch(e=>{if(active){setError(e.message);setMode('error')}})
    const expired=()=>{setMode('login');setError('登录已过期，请重新登录；云端任务会继续执行')}
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
  if(mode==='ready')return <><div className="cloud-account"><span>{pending||'云端工程工作区 · 数据已隔离'}</span><button onClick={()=>location.reload()}>刷新</button><button onClick={async()=>{await fetch('/api/auth/logout',{method:'POST'});localStorage.removeItem('engineering-active-task');setMode('login')}}>退出登录</button></div>{children}</>
  return <div className="cloud-login"><form onSubmit={async e=>{
    e.preventDefault();setBusy(true);setError('')
    try {const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email,password})});if(!r.ok)throw new Error('邮箱或密码错误，或账号尚未开通');setPassword('');setMode('ready')}
    catch(e){setError(e instanceof Error?e.message:'登录失败')}finally{setBusy(false)}
  }}><h1>工程解析平台</h1><p>登录云端工程工作区</p>
    {mode==='loading'?<p>正在连接…</p>:mode==='error'?<><p role="alert">{error}</p><button type="button" onClick={()=>location.reload()}>重新连接</button></>:<>
    <label>邮箱<input type="email" autoComplete="username" value={email} onChange={e=>setEmail(e.target.value)} required/></label>
    <label>密码<input type="password" autoComplete="current-password" value={password} onChange={e=>setPassword(e.target.value)} required/></label>
    {error&&<p role="alert">{error}</p>}<button disabled={busy}>{busy?'正在登录…':'登录'}</button><small>请使用管理员在 Supabase 中开通的账号</small></>}
  </form></div>
}
