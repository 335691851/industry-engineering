import { Container } from '@cloudflare/containers';
import { env, WorkflowEntrypoint, type WorkflowEvent, type WorkflowStep } from 'cloudflare:workers';

type Task = { taskId: string; ownerId: string };

async function backend(env: Env, request: Request, instance: string): Promise<Response> {
  if (env.PYTHON_BACKEND_URL) {
    const base = new URL(env.PYTHON_BACKEND_URL);
    if (base.protocol !== 'https:') throw new Error('HTTPS backend required');
    const incoming = new URL(request.url);
    base.pathname = incoming.pathname; base.search = incoming.search;
    return fetch(new Request(base, request));
  }
  return env.ENGINEERING_RUNTIME.getByName(instance).fetch(request);
}

export class EngineeringRuntime extends Container {
  defaultPort = 8080;
  sleepAfter = '20m';
  envVars = {
    ENGINEERING_RUNTIME: 'cloud', SUPABASE_URL: env.SUPABASE_URL,
    SUPABASE_PUBLISHABLE_KEY: env.SUPABASE_PUBLISHABLE_KEY, SUPABASE_DB_URL: env.SUPABASE_DB_URL,
    SUPABASE_SECRET_KEY: env.SUPABASE_SECRET_KEY, SUPABASE_STORAGE_BUCKET: env.SUPABASE_STORAGE_BUCKET,
    ENGINEERING_APP_ORIGIN: env.ENGINEERING_APP_ORIGIN, ENGINEERING_SERVICE_TOKEN: env.ENGINEERING_SERVICE_TOKEN,
    CLOUDFLARE_DISPATCH_URL: env.CLOUDFLARE_DISPATCH_URL, DEEPSEEK_API_KEY: env.DEEPSEEK_API_KEY,
  };
}

export class EngineeringTask extends WorkflowEntrypoint<Env, Task> {
  async run(event: WorkflowEvent<Task>, step: WorkflowStep) {
    return step.do('execute persisted engineering task', {
      retries: { limit: 5, delay: '30 seconds', backoff: 'exponential' }, timeout: '30 minutes',
    }, async () => {
      const task = event.payload;
      const result = await backend(this.env, new Request(`http://runtime/api/internal/tasks/${task.taskId}`, {
        method:'POST', headers: { Authorization: `Bearer ${this.env.ENGINEERING_SERVICE_TOKEN}`, 'X-Engineering-Owner':task.ownerId },
      }), 'jobs');
      if (!result.ok) throw new Error(`engineering_task_http_${result.status}`);
      // Large engineering results stay in Postgres/Storage, not Workflow step payloads.
      return { taskId: task.taskId, acknowledged: true };
    });
  }
}

export default {
  async scheduled(_event: ScheduledController, env: Env): Promise<void> {
    const response=await backend(env, new Request('http://runtime/api/internal/outbox',{
      method:'POST',headers:{Authorization:`Bearer ${env.ENGINEERING_SERVICE_TOKEN}`},
    }), 'web');
    if(!response.ok)throw new Error('outbox_read_failed');
    const tasks=await response.json<{id:string;owner_id:string}[]>();
    for(const task of tasks){
      try{await env.ENGINEERING_TASKS.create({id:task.id,params:{taskId:task.id,ownerId:task.owner_id}})}
      catch{await (await env.ENGINEERING_TASKS.get(task.id)).status()}
    }
  },
  async fetch(request: Request, env: Env): Promise<Response> {
    const path = new URL(request.url).pathname;
    if (path === '/dispatch' && request.method === 'POST') {
      const secret = env.ENGINEERING_SERVICE_TOKEN || '';
      if (secret.length < 32) return new Response('not configured', {status:503});
      const expected = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(`Bearer ${secret}`));
      const actual = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(request.headers.get('Authorization') || ''));
      if (!crypto.subtle.timingSafeEqual(expected, actual)) return new Response('unauthorized', {status:401});
      const reader=request.body?.getReader();
      if(!reader)return new Response('empty',{status:400});
      const chunks:Uint8Array[]=[];let size=0;
      while(true){const {done,value}=await reader.read();if(done)break;size+=value.byteLength;if(size>1024){await reader.cancel();return new Response('too large',{status:413})}chunks.push(value)}
      const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length}
      let task:Task;
      try { task=JSON.parse(new TextDecoder().decode(bytes)); } catch {return new Response('invalid json',{status:400})}
      if(!/^[a-f0-9]{12}$/.test(task.taskId) || !/^[a-f0-9-]{36}$/i.test(task.ownerId))return new Response('invalid task',{status:400});
      try { await env.ENGINEERING_TASKS.create({id:task.taskId,params:task}); }
      catch {
        try { await (await env.ENGINEERING_TASKS.get(task.taskId)).status(); }
        catch { return new Response('dispatch failed',{status:503}); }
      }
      return Response.json({id:task.taskId},{status:202});
    }
    if (!path.startsWith('/api/') || path.startsWith('/api/internal/')) return new Response('not found',{status:404});
    return backend(env, request, 'web');
  },
} satisfies ExportedHandler<Env>;
