// Contract tests use an in-memory Supabase/Workflow transport. No external account required.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';

const bundle = await build({
  entryPoints: ['src/index.ts'], bundle: true, write: false, format: 'esm', platform: 'node',
  plugins: [{ name: 'test-workflow-runtime', setup(builder) {
    builder.onResolve({ filter: /^cloudflare:workers$/ }, () => ({ path: 'runtime', namespace: 'test' }));
    builder.onLoad({ filter: /.*/, namespace: 'test' }, () => ({ contents:
      'export class WorkflowEntrypoint { constructor(ctx, env) { this.env = env; } }' }));
  } }],
});
const { default: gateway, EngineeringValidation } = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);
const ownerA = 'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa';
const ownerB = 'bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb';
const jobId = 'cccccccc-cccc-4ccc-cccc-cccccccccccc';
const jobs = new Map();
let dispatches = 0;
const env = {
  APP_ORIGIN: 'https://app.example.com', SUPABASE_URL: 'https://db.example.com',
  SUPABASE_PUBLISHABLE_KEY: 'public', SUPABASE_SECRET_KEY: 'sb_secret_test',
  COMPUTE_URL: 'https://compute.example.com/engineering-model', ENGINEERING_COMPUTE_TOKEN: 'x'.repeat(40),
  ENGINEERING_VALIDATION: { create: async () => { dispatches++; }, get: async () => ({ status: async () => ({ status: 'running' }) }) },
};
const originalFetch = globalThis.fetch;
globalThis.fetch = async (url, options = {}) => {
  const target = new URL(url);
  if (target.pathname === '/auth/v1/user') {
    const token = options.headers.Authorization;
    return token === 'Bearer A' ? Response.json({ id: ownerA }) : token === 'Bearer B' ? Response.json({ id: ownerB }) : new Response('', { status: 401 });
  }
  if (target.host === 'compute.example.com') {
    assert.equal(options.headers.Authorization, `Bearer ${env.ENGINEERING_COMPUTE_TOKEN}`);
    return Response.json({ validation: { status: 'pending_human_review', errors: [] } });
  }
  assert.equal(target.pathname, '/rest/v1/cloud_validation_jobs');
  assert.equal(options.headers.apikey, 'sb_secret_test');
  if (options.method === 'POST') {
    const row = JSON.parse(options.body);
    if (!jobs.has(row.id)) jobs.set(row.id, { ...row, report: null, error: '' });
    return new Response(null, { status: 201 });
  }
  const id = target.searchParams.get('id')?.slice(3);
  const owner = target.searchParams.get('owner_id')?.slice(3);
  assert.ok(owner, 'Privileged queries must always scope owner_id');
  const row = jobs.get(id);
  if (options.method === 'PATCH') {
    if (row?.owner_id === owner) Object.assign(row, JSON.parse(options.body));
    return new Response(null, { status: 204 });
  }
  return Response.json(row?.owner_id === owner ? [row] : []);
};

function request(method, path, token, value, key = jobId) {
  return new Request(`https://worker.example.com${path}`, {
    method, headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', 'Idempotency-Key': key },
    body: value ? JSON.stringify(value) : undefined,
  });
}

test('Auth, ownership, immutable inputs and workflow persistence', async () => {
  try {
    let response = await gateway.fetch(request('POST', '/api/cloud/validation-jobs', 'bad', { geometry: {} }), env);
    assert.equal(response.status, 401);
    assert.equal(jobs.size, 0);
    response = await gateway.fetch(request('POST', '/api/cloud/validation-jobs', 'A', { geometry: {} }), env);
    assert.equal(response.status, 202);
    assert.equal(jobs.size, 1);
    assert.equal(dispatches, 1);
    response = await gateway.fetch(request('GET', `/api/cloud/validation-jobs/${jobId}`, 'B'), env);
    assert.equal(response.status, 404);
    response = await gateway.fetch(request('POST', '/api/cloud/validation-jobs', 'B', { geometry: {} }), env);
    assert.equal(response.status, 404);
    response = await gateway.fetch(request('POST', '/api/cloud/validation-jobs', 'A', { geometry: { overall_length_mm: 123 } }), env);
    assert.equal(response.status, 409);
    assert.deepEqual(jobs.get(jobId).input, { geometry: {} });
    const flow = new EngineeringValidation({}, env);
    await flow.run({ payload: { jobId, ownerId: ownerA } }, { do: async (_name, ...args) => args.at(-1)() });
    assert.equal(jobs.get(jobId).status, 'completed');
    assert.equal(jobs.get(jobId).report.validation.status, 'pending_human_review');
    response = await gateway.fetch(request('POST', '/api/cloud/validation-jobs', 'A', { geometry: {} }), env);
    assert.equal(response.status, 202);
    assert.equal(dispatches, 1, 'Completed request must not dispatch again');
  } finally { globalThis.fetch = originalFetch; }
});
