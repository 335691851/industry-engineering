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
