import {test} from 'node:test';
import assert from 'node:assert/strict';
import {authorized,validTask,backend} from '../lib/service.mjs';
test('service authentication and task scope validation',()=>{
  process.env.ENGINEERING_SERVICE_TOKEN='a'.repeat(32);
  assert.equal(authorized(new Request('https://app.test')),false);
  assert.equal(authorized(new Request('https://app.test',{headers:{Authorization:'Bearer '+'a'.repeat(32)}})),true);
  assert.equal(validTask({taskId:'abcdef123456',ownerId:'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'}),true);
  assert.equal(validTask({taskId:'../../api',ownerId:'a'.repeat(36)}),false);
});
test('backend request uses private binding and service identity',async()=>{
  process.env.ENGINEERING_BACKEND_URL='https://backend.internal';
  const old=globalThis.fetch;
  globalThis.fetch=async(url,options)=>{
    assert.equal(url.toString(),'https://backend.internal/api/internal/tasks/a');
    assert.equal(options.headers['X-Engineering-Owner'],'owner');
    assert.equal(options.cache,'no-store');
    return new Response('{}');
  };
  try{await backend('/api/internal/tasks/a',undefined,'owner')}finally{globalThis.fetch=old}
});
