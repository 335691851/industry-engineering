// Browser calls remain same-origin; internal service routes are never proxied.
export const maxDuration = 300;
export const dynamic = 'force-dynamic';
async function proxy(request) {
  const incoming = new URL(request.url);
  if (incoming.pathname.startsWith('/api/internal/')) return new Response(null, {status:404});
  const base = process.env.ENGINEERING_BACKEND_URL;
  if (!base) return Response.json({detail:'尚未配置 Agent 服务地址'}, {status:503});
  const headers = new Headers();
  for (const key of ['content-type','cookie','origin','accept']) {
    const value = request.headers.get(key); if(value) headers.set(key,value);
  }
  if(process.env.ENGINEERING_BACKEND_BYPASS) headers.set('x-vercel-protection-bypass',process.env.ENGINEERING_BACKEND_BYPASS);
  try {
    const response = await fetch(new URL(incoming.pathname + incoming.search, base), {
      method:request.method, headers, redirect:'manual', cache:'no-store',
      body:['GET','HEAD'].includes(request.method)?undefined:await request.arrayBuffer(),
      signal:AbortSignal.timeout(270000),
    });
    const outgoing = new Headers({'cache-control':'private, no-store'});
    for(const key of ['content-type','content-disposition','location']) {
      const value=response.headers.get(key); if(value) outgoing.set(key,value);
    }
    for(const value of response.headers.getSetCookie()) outgoing.append('set-cookie',value);
    return new Response(response.body,{status:response.status,headers:outgoing});
  } catch {
    return Response.json({detail:'Agent 服务连接失败；后台任务可在恢复连接后继续查看'}, {status:503});
  }
}
export {proxy as GET,proxy as POST,proxy as PATCH,proxy as PUT,proxy as DELETE,proxy as HEAD};
