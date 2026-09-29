import {createHmac,timingSafeEqual} from 'node:crypto';
// Browser calls remain same-origin. One service-authenticated route transports
// tenant files between the isolated Agent and Engineering containers.
export const maxDuration = 300;
export const dynamic = 'force-dynamic';
async function proxy(request) {
  const incoming = new URL(request.url);
  const nativeStorage=incoming.pathname==='/api/internal/native-storage';
  if (incoming.pathname.startsWith('/api/internal/') && !nativeStorage) return new Response(null, {status:404});
  const base = process.env.ENGINEERING_BACKEND_URL;
  if (!base) return Response.json({detail:'灏氭湭閰嶇疆 Agent 鏈嶅姟鍦板潃'}, {status:503});
  const headers = new Headers();
  for (const key of ['content-type','cookie','origin','accept']) {
    const value = request.headers.get(key); if(value) headers.set(key,value);
  }
  const secret=process.env.ENGINEERING_SERVICE_TOKEN || '';
  if(secret.length<32) return Response.json({detail:'platform 缺少服务端访问保护配置 ENGINEERING_SERVICE_TOKEN'}, {status:503});
  if(nativeStorage){
    const supplied=request.headers.get('authorization') || '';
    const expected=`Bearer ${secret}`;
    if(supplied.length!==expected.length || !timingSafeEqual(Buffer.from(supplied),Buffer.from(expected)))
      return new Response(null,{status:401});
    const owner=request.headers.get('x-engineering-owner') || '';
    if(!/^[0-9a-f-]{36}$/i.test(owner))return new Response(null,{status:401});
    headers.set('authorization',supplied);
    headers.set('x-engineering-owner',owner);
  }
  const ip=(request.headers.get('x-vercel-forwarded-for') || '').split(',')[0].trim() || '127.0.0.1';
  const stamp=String(Math.floor(Date.now()/1000));
  const proof=createHmac('sha256',secret).update(`${stamp}\n${request.method}\n${incoming.pathname}\n${ip}`).digest('hex');
  if(!nativeStorage){
    headers.set('x-engineering-client',ip);
    headers.set('x-engineering-time',stamp);
    headers.set('x-engineering-proof',proof);
  }
  if(process.env.ENGINEERING_BACKEND_BYPASS) headers.set('x-vercel-protection-bypass',process.env.ENGINEERING_BACKEND_BYPASS);
  try {
    const response = await fetch(new URL(incoming.pathname + incoming.search, base), {
      method:request.method, headers, redirect:'manual', cache:'no-store',
      body:['GET','HEAD'].includes(request.method)?undefined:await request.arrayBuffer(),
      signal:AbortSignal.timeout(270000),
    });
    if (incoming.pathname === '/api/deployment') {
      const type = response.headers.get('content-type') || '';
      const location = response.headers.get('location') || '';
      let detail = '';
      if (response.status === 404) detail = 'Agent 服务地址返回 404：请检查 platform 的 ENGINEERING_BACKEND_URL 是否为有效后端域名，以及 agent-api 是否已部署容器入口';
      else if ([401,403].includes(response.status) || /vercel\.com\/(login|sso)/.test(location)) detail = 'Agent 服务被部署保护拦截：请检查 platform 的 ENGINEERING_BACKEND_BYPASS，并重新部署';
      else if (!response.ok) detail = `Agent 服务异常（HTTP ${response.status}），请检查 agent-api 运行日志`;
      else if (!type.includes('application/json')) detail = 'Agent 地址返回了网页，未返回接口数据：请检查 ENGINEERING_BACKEND_URL 和部署入口';
      if (detail) return Response.json({detail}, {status:502,headers:{'cache-control':'private, no-store'}});
    }
    const outgoing = new Headers({'cache-control':'private, no-store'});
    for(const key of ['content-type','content-disposition','location','retry-after']) {
      const value=response.headers.get(key); if(value) outgoing.set(key,value);
    }
    for(const value of response.headers.getSetCookie()) outgoing.append('set-cookie',value);
    return new Response(response.body,{status:response.status,headers:outgoing});
  } catch {
    return Response.json({detail:'Agent 鏈嶅姟杩炴帴澶辫触锛涘悗鍙颁换鍔″彲鍦ㄦ仮澶嶈繛鎺ュ悗缁х画鏌ョ湅'}, {status:503});
  }
}
export {proxy as GET,proxy as POST,proxy as PATCH,proxy as PUT,proxy as DELETE,proxy as HEAD};
