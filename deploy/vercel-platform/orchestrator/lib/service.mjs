import { timingSafeEqual } from 'node:crypto';

export function authorized(request,name='ENGINEERING_SERVICE_TOKEN') {
  const secret=process.env[name]||'';
  const supplied=Buffer.from(request.headers.get('authorization')||'');
  const expected=Buffer.from(`Bearer ${secret}`);
  return secret.length>=32 && supplied.length===expected.length && timingSafeEqual(supplied,expected);
}
export function validTask(value) {
  return /^[a-f0-9]{12}$/.test(value?.taskId||'') &&
    /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(value?.ownerId||'');
}
export async function backend(path,body,ownerId,timeout=650000) {
  const base=process.env.ENGINEERING_BACKEND_URL;
  if(!base)throw new Error('Backend service binding missing');
  return fetch(new URL(path.replace(/^\//,''),base.replace(/\/$/,'')+'/'),{method:'POST',cache:'no-store',
    headers:{'Content-Type':'application/json',Authorization:`Bearer ${process.env.ENGINEERING_SERVICE_TOKEN}`,
      ...(ownerId?{'X-Engineering-Owner':ownerId}:{})},
    body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(timeout)});
}
