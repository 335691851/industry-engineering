import { WorkflowEntrypoint, type WorkflowEvent, type WorkflowStep } from 'cloudflare:workers';

type Input = { geometry: Record<string, unknown>; part?: Record<string, unknown> };
type Params = { jobId: string; ownerId: string };
type Job = { id: string; owner_id: string; input: Input; input_hash: string; status: string; report: unknown; error: string };
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

class HttpError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function readJson(source: Request | Response, limit = 200_000): Promise<unknown> {
  if (!source.body) throw new HttpError(400, '请求为空');
  const reader = source.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) { await reader.cancel(); throw new HttpError(413, '工程数据过大，请按部件拆分'); }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  try { return JSON.parse(new TextDecoder().decode(bytes)); }
  catch { throw new HttpError(400, 'JSON 格式无效'); }
}

function object(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

function configured(env: Env) {
  if (!env.SUPABASE_URL.startsWith('https://') || !env.COMPUTE_URL.startsWith('https://') ||
      !env.SUPABASE_PUBLISHABLE_KEY || !env.SUPABASE_SECRET_KEY || env.ENGINEERING_COMPUTE_TOKEN.length < 32) {
    throw new HttpError(503, '云端服务尚未配置完成');
  }
}

async function owner(request: Request, env: Env): Promise<string> {
  const authorization = request.headers.get('Authorization') || '';
  if (!authorization.startsWith('Bearer ')) throw new HttpError(401, '请先登录');
  // Validate with Auth, never trust a decoded but unverified JWT or user_metadata.
  const response = await fetch(`${env.SUPABASE_URL}/auth/v1/user`, {
    headers: { apikey: env.SUPABASE_PUBLISHABLE_KEY, Authorization: authorization },
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new HttpError(response.status >= 500 ? 503 : 401, '登录状态验证失败');
  const user = await readJson(response, 100_000);
  if (!object(user) || typeof user.id !== 'string' || !UUID.test(user.id)) throw new HttpError(401, '无效用户');
  return user.id;
}

async function database(env: Env, path: string, method = 'GET', value?: unknown, prefer?: string): Promise<Response> {
  const response = await fetch(`${env.SUPABASE_URL}/rest/v1/cloud_validation_jobs${path}`, {
    method,
    headers: {
      apikey: env.SUPABASE_SECRET_KEY,
      ...(env.SUPABASE_SECRET_KEY.startsWith('eyJ') ? { Authorization: `Bearer ${env.SUPABASE_SECRET_KEY}` } : {}),
      'Content-Type': 'application/json',
      ...(prefer ? { Prefer: prefer } : {}),
    },
    body: value === undefined ? undefined : JSON.stringify(value),
    signal: AbortSignal.timeout(15_000),
  });
  if (!response.ok) throw new HttpError(503, '云端数据读写失败');
  return response;
}

function filter(params: Params): string {
  // Every privileged read/write is additionally scoped by the verified owner ID.
  return `?id=eq.${encodeURIComponent(params.jobId)}&owner_id=eq.${encodeURIComponent(params.ownerId)}`;
}

async function getJob(env: Env, params: Params): Promise<Job> {
  const data = await readJson(await database(env, filter(params)), 600_000);
  if (!Array.isArray(data) || data.length !== 1) throw new HttpError(404, '校核任务不存在');
  return data[0] as Job;
}

async function updateJob(env: Env, params: Params, patch: Record<string, unknown>) {
  await database(env, filter(params), 'PATCH', { ...patch, updated_at: new Date().toISOString() });
}

export class EngineeringValidation extends WorkflowEntrypoint<Env, Params> {
  async run(event: WorkflowEvent<Params>, step: WorkflowStep) {
    const params = event.payload;
    try {
      const inputJson = await step.do('load immutable input', async () => JSON.stringify((await getJob(this.env, params)).input));
      const result = await step.do('compute and persist report', {
        retries: { limit: 2, delay: '5 seconds', backoff: 'exponential' }, timeout: '60 seconds',
      }, async () => {
        await updateJob(this.env, params, { status: 'running' });
        const response = await fetch(this.env.COMPUTE_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${this.env.ENGINEERING_COMPUTE_TOKEN}` },
          body: inputJson, signal: AbortSignal.timeout(35_000),
        });
        if (!response.ok) {
          if (response.status === 422 || response.status === 413) {
            await updateJob(this.env, params, { status: 'failed', error: '工程参数无效或超限，请修改后提交新任务' });
            return { status: 'failed' };
          }
          throw new Error(`compute_http_${response.status}`);
        }
        const report = await readJson(response, 350_000);
        if (!object(report) || !object(report.validation) ||
            !['blocked', 'pending_human_review'].includes(String(report.validation.status))) {
          throw new Error('invalid_compute_report');
        }
        // Completed is computation completion only; NEVER an engineering approval.
        await updateJob(this.env, params, { status: 'completed', report, error: '' });
        return { status: 'completed' };
      });
      return { jobId: params.jobId, ...result };
    } catch (error) {
      await step.do('record failure', async () => {
        await updateJob(this.env, params, { status: 'failed', error: '云端校核失败，请检查计算服务配置或稍后提交新任务' });
      });
      throw error;
    }
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const origin = request.headers.get('Origin');
    const headers = new Headers({ 'Cache-Control': 'no-store', Vary: 'Origin' });
    if (origin && origin === env.APP_ORIGIN) {
      headers.set('Access-Control-Allow-Origin', origin);
      headers.set('Access-Control-Allow-Headers', 'Authorization, Content-Type, Idempotency-Key');
      headers.set('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
    }
    const reply = (value: unknown, status = 200) => Response.json(value, { status, headers });
    try {
      if (origin && origin !== env.APP_ORIGIN) throw new HttpError(403, '当前站点不允许访问');
      if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers });
      const url = new URL(request.url);
      if (url.pathname === '/api/cloud/capabilities' && request.method === 'GET') {
        return reply({ migration: 'validation_only', engineering_validation: true,
          business_api: false, agent_generation: false, native_ocr: false, native_cad_conversion: false });
      }
      configured(env);
      const ownerId = await owner(request, env);
      if (request.method === 'POST' && url.pathname === '/api/cloud/validation-jobs') {
        const jobId = request.headers.get('Idempotency-Key') || '';
        if (!UUID.test(jobId)) throw new HttpError(400, '请提供 UUID 格式的 Idempotency-Key；重试时保持相同值');
        const input = await readJson(request);
        if (!object(input) || !object(input.geometry) || (input.part !== undefined && !object(input.part)) ||
            Object.keys(input).some(key => !['geometry', 'part'].includes(key))) throw new HttpError(422, '工程参数格式错误');
        const hashBytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(input)));
        const inputHash = Array.from(new Uint8Array(hashBytes), b => b.toString(16).padStart(2, '0')).join('');
        await database(env, '?on_conflict=id', 'POST', {
          id: jobId, owner_id: ownerId, input, input_hash: inputHash, status: 'queued',
        }, 'resolution=ignore-duplicates,return=minimal');
        const params = { jobId, ownerId };
        const job = await getJob(env, params);
        if (job.input_hash !== inputHash) throw new HttpError(409, '同一任务编号的输入已固定，请使用新的任务编号');
        if (job.status === 'queued') {
          try { await env.ENGINEERING_VALIDATION.create({ id: jobId, params }); }
          catch {
            // A previous request may have created the workflow then lost its HTTP response.
            try { await (await env.ENGINEERING_VALIDATION.get(jobId)).status(); }
            catch { throw new HttpError(503, '任务已保存但调度失败，请使用相同任务编号重试'); }
          }
        }
        return reply({ id: jobId, status: job.status, status_url: `/api/cloud/validation-jobs/${jobId}` }, 202);
      }
      const match = url.pathname.match(/^\/api\/cloud\/validation-jobs\/([^/]+)$/);
      if (request.method === 'GET' && match && UUID.test(match[1])) {
        const job = await getJob(env, { jobId: match[1], ownerId });
        return reply({ id: job.id, status: job.status, report: job.report, error: job.error });
      }
      throw new HttpError(404, '该云端接口尚未提供');
    } catch (error) {
      const status = error instanceof HttpError ? error.status : 503;
      return reply({ error: error instanceof HttpError ? error.message : '云端服务暂时不可用' }, status);
    }
  },
} satisfies ExportedHandler<Env>;
