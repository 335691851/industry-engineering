let refreshing: Promise<boolean> | null = null
let cloudMode: Promise<boolean> | null = null
export async function authenticatedFetch(path: string, options?: RequestInit): Promise<Response> {
  if(typeof options?.body==='string' && options.body.length>1_000_000 && /^\/api\/parts\/[a-f0-9]+\/cad\/studio\/[a-f0-9]+\/(baseline|save)$/.test(path)){
    cloudMode ??= fetch('/api/deployment').then(r=>r.json()).then(c=>!!c.authentication)
    if(await cloudMode){
      const form=new FormData()
      form.set('file',new File([options.body],'cad-edit.json',{type:'application/json'}))
      return authenticatedFetch(path,{method:'POST',body:form})
    }
  }
  if(options?.body instanceof FormData){
    cloudMode ??= fetch('/api/deployment').then(r=>r.json()).then(c=>!!c.authentication)
    if(await cloudMode){
      const form=options.body, file=form.get('file')
      if(!(file instanceof File))throw new Error('请选择上传文件')
      const prepared=await authenticatedFetch('/api/uploads/prepare',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({target:path,name:file.name,size:file.size})})
      if(!prepared.ok)return prepared
      const value=await prepared.json()
      const upload=await fetch(value.url,{method:'PUT',headers:{'Content-Type':file.type||'application/octet-stream'},body:file})
      if(!upload.ok)throw new Error('文件上传失败，请重试')
      const fields:Record<string,string>={}
      form.forEach((v,k)=>{if(typeof v==='string')fields[k]=v})
      return authenticatedFetch('/api/uploads/complete',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({ticket:value.ticket,fields})})
    }
  }
  let response = await fetch(path, options)
  if (response.status === 401 && !['/api/auth/login','/api/auth/refresh'].includes(path)) {
    refreshing ??= fetch('/api/auth/refresh', {method:'POST'}).then(r => r.ok).finally(() => { refreshing = null })
    if (await refreshing) response = await fetch(path, options)
    else window.dispatchEvent(new Event('engineering-session-expired'))
  }
  return response
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await authenticatedFetch(path, options)
  const value = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : `请求失败 (${response.status})`)
  if (response.status === 202 && value.cloud_task_id) {
    localStorage.setItem('engineering-active-task', value.cloud_task_id)
    return waitTask<T>(value.cloud_task_id)
  }
  return value as T
}

export async function waitTask<T>(id: string): Promise<T> {
  for (let attempt = 0; attempt < 900; attempt++) {
    const task = await api<{state:string;result:T;error:string}>(`/api/cloud/tasks/${id}`)
    if (task.state === 'failed') {
      localStorage.removeItem('engineering-active-task')
      throw new Error(task.error || '工程任务失败')
    }
    if (task.state === 'completed') {
      localStorage.removeItem('engineering-active-task')
      return task.result
    }
    await new Promise(resolve => setTimeout(resolve, 2000))
  }
  throw new Error('任务仍在云端执行，可刷新页面继续查看结果')
}
