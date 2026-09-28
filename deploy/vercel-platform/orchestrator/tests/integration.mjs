// Run after npm run build. Uses a disposable backend stub; never calls a model or database.
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import assert from 'node:assert/strict';
const secret='integration-only-'.repeat(3);
let calls=0;
const backend=createServer((request,response)=>{
  assert.equal(request.headers.authorization,`Bearer ${secret}`);
  assert.equal(request.headers['x-engineering-owner'],'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa');
  assert.equal(request.url,'/api/internal/tasks/abcdef123456');
  calls++;response.setHeader('Content-Type','application/json');response.end('{"state":"completed"}');
});
await new Promise(resolve=>backend.listen(3308,'127.0.0.1',resolve));
const child=spawn(process.execPath,['node_modules/next/dist/bin/next','start','-p','3307'],{
  env:{...process.env,ENGINEERING_SERVICE_TOKEN:secret,ENGINEERING_BACKEND_URL:'http://127.0.0.1:3308',PORT:'3307'},stdio:['ignore','pipe','pipe']
});
let output='';child.stdout.on('data',x=>output=(output+x).slice(-6000));child.stderr.on('data',x=>output=(output+x).slice(-6000));
const pause=()=>new Promise(r=>setTimeout(r,300));
try{
  let ready=false;
  for(let i=0;i<100;i++){try{ready=(await fetch('http://127.0.0.1:3307')).ok}catch{}if(ready)break;await pause()}
  assert.ok(ready,'Next server did not start');
  const result=await fetch('http://127.0.0.1:3307/api/cloud/dispatch',{method:'POST',headers:{Authorization:`Bearer ${secret}`,'Content-Type':'application/json'},body:JSON.stringify({taskId:'abcdef123456',ownerId:'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'})});
  assert.equal(result.status,202,await result.text());
  for(let i=0;i<100 && !calls;i++)await pause();
  assert.equal(calls,1,'Workflow did not execute the backend step');
  console.log('PASS: HTTP dispatch → durable Workflow → authenticated backend step');
}catch(error){console.error(output);throw error}
finally{child.kill();await new Promise(resolve=>backend.close(resolve))}
