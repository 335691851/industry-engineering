process.env.ENGINEERING_SERVICE_TOKEN='test-service-token-at-least-32-characters';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {GET,POST} from '../app/api/[...path]/route.js';
test('proxy blocks internal access and preserves cookies and origin',async()=>{
  assert.equal((await GET(new Request('https://platform.test/api/internal/outbox'))).status,404);
  process.env.ENGINEERING_BACKEND_URL='https://agent.test';
  const original=globalThis.fetch;
  globalThis.fetch=async(url,options)=>{
    assert.equal(url.toString(),'https://agent.test/api/auth/login');
    assert.equal(options.headers.get('origin'),'https://platform.test');
    assert.equal(options.headers.has('authorization'),false);
    assert.equal(options.headers.get('x-engineering-proof').length,64);
    const headers=new Headers();
    headers.append('set-cookie','engineering_access=one; HttpOnly; Secure; Path=/');
    headers.append('set-cookie','engineering_refresh=two; HttpOnly; Secure; Path=/api/auth');
    return new Response('{}',{headers});
  };
  try{
    const response=await POST(new Request('https://platform.test/api/auth/login',{
      method:'POST',headers:{origin:'https://platform.test',authorization:'untrusted'},body:'{}'}));
    assert.equal(response.headers.getSetCookie().length,2);
    assert.equal(response.headers.get('cache-control'),'private, no-store');
  }finally{globalThis.fetch=original}
});

test('native storage proxy requires the shared service identity',async()=>{
  process.env.ENGINEERING_BACKEND_URL='https://agent.test';
  const original=globalThis.fetch;
  const owner='aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa';
  globalThis.fetch=async(url,options)=>{
    assert.equal(url.toString(),'https://agent.test/api/internal/native-storage?key='+owner+'%2Frpc%2Ffile.json');
    assert.equal(options.headers.get('authorization'),'Bearer '+process.env.ENGINEERING_SERVICE_TOKEN);
    assert.equal(options.headers.get('x-engineering-owner'),owner);
    assert.equal(options.headers.has('x-engineering-proof'),false);
    return new Response('artifact');
  };
  try{
    const url='https://platform.test/api/internal/native-storage?key='+owner+'%2Frpc%2Ffile.json';
    assert.equal((await GET(new Request(url))).status,401);
    const response=await GET(new Request(url,{headers:{authorization:'Bearer '+process.env.ENGINEERING_SERVICE_TOKEN,'x-engineering-owner':owner}}));
    assert.equal(response.status,200);
    assert.equal(await response.text(),'artifact');
  }finally{globalThis.fetch=original}
});


test('deployment probe explains upstream deployment failures',async()=>{
  process.env.ENGINEERING_BACKEND_URL='https://agent.test';
  const original=globalThis.fetch;
  try {
    for (const [status,type,body,expected] of [
      [404,'text/plain','NOT_FOUND','404'],
      [401,'application/json','{}','部署保护'],
      [200,'text/html','<html>login</html>','网页'],
    ]) {
      globalThis.fetch=async()=>new Response(body,{status,headers:{'content-type':type}});
      const r=await GET(new Request('https://platform.test/api/deployment'));
      assert.equal(r.status,502);
      assert.ok((await r.json()).detail.includes(expected));
    }
    globalThis.fetch=async()=>Response.json({runtime:'cloud',authentication:true});
    const r=await GET(new Request('https://platform.test/api/deployment'));
    assert.equal(r.status,200);
    assert.equal((await r.json()).authentication,true);
  } finally {globalThis.fetch=original}
});
