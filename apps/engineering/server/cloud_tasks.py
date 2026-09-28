"""Durable task outbox and project-level serialization, independent of process threads."""
import json
import os
import re
import httpx
from .cloud_context import owner
from .db import connect, row, uid, now

LONG_ROUTE = re.compile(r'^/api/(?:chat|projects/[a-f0-9]+/analyze|parts/[a-f0-9]+/generate/(?:drawing|process))$')


def dispatch(task_id):
    if os.getenv('ENGINEERING_TASK_PROVIDER') == 'vercel':
        from urllib.parse import urljoin
        base = os.environ['ENGINEERING_ORCHESTRATOR_URL']
        # Serialize dispatch; a lost HTTP reply may create another run, but execution is idempotent.
        with connect() as con:
            item = row(con, 'SELECT id,state,workflow_run_id FROM cloud_tasks WHERE id=? FOR UPDATE', (task_id,))
            if not item or item['state'] in ('completed', 'failed'): return
            response = httpx.post(urljoin(base.rstrip('/')+'/', 'api/cloud/dispatch'),
                headers={'Authorization': 'Bearer ' + os.environ['ENGINEERING_SERVICE_TOKEN'],
                         **({'x-vercel-protection-bypass':os.environ['ENGINEERING_ORCHESTRATOR_BYPASS']} if os.getenv('ENGINEERING_ORCHESTRATOR_BYPASS') else {})},
                json={'taskId': task_id, 'ownerId': owner(), 'previousRunId':item['workflow_run_id']}, timeout=20)
            response.raise_for_status()
            con.execute('UPDATE cloud_tasks SET workflow_run_id=?,updated_at=? WHERE id=?',
                        (response.json()['runId'], now(), task_id))
        return
    response = httpx.post(os.environ['CLOUDFLARE_DISPATCH_URL'],
        headers={'Authorization': 'Bearer ' + os.environ['ENGINEERING_SERVICE_TOKEN']},
        json={'taskId': task_id, 'ownerId': owner()}, timeout=20)
    if not response.is_success:
        raise RuntimeError('任务已持久化，但调度未成功；在任务状态页重试即可')


def enqueue(project_id, operation, payload):
    task_id = uid()
    with connect() as con:
        if not row(con, 'SELECT id FROM projects WHERE id=?', (project_id,)):
            raise ValueError('项目不存在')
        con.execute('INSERT INTO cloud_tasks (id,project_id,operation,payload,created_at,updated_at) VALUES (?,?,?,?,?,?)',
                    (task_id, project_id, operation, json.dumps(payload, ensure_ascii=False), now(), now()))
    try: dispatch(task_id)
    except (RuntimeError, httpx.HTTPError): pass  # The outbox remains queued; polling retries dispatch.
    return {'cloud_task_id': task_id, 'state': 'queued'}


def enqueue_workflow(job_id, project_id):
    return enqueue(project_id, 'workflow', {'job_id': job_id})


def task(task_id):
    with connect() as con:
        return row(con, 'SELECT * FROM cloud_tasks WHERE id=?', (task_id,))


def update(task_id, **fields):
    fields['updated_at'] = now()
    with connect() as con:
        con.execute('UPDATE cloud_tasks SET ' + ','.join(f'{key}=?' for key in fields) + ' WHERE id=?',
                    (*fields.values(), task_id))


def fail(item, reason):
    update(item['id'], state='failed', error=reason)
    if item['operation'] == 'workflow':
        payload = json.loads(item['payload'])
        with connect() as con:
            con.execute("UPDATE jobs SET status='中断',current=?,errors=?,updated_at=? WHERE id=?",
                        (reason,json.dumps([reason],ensure_ascii=False),now(),payload['job_id']))
