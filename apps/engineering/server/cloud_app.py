"""Authenticated cloud adapter around the complete engineering business API.

Supabase owns durable state; each HTTP request receives an isolated temporary workspace.
Long requests are persisted and executed by the configured Workflow service, not daemon threads.
"""
import hmac
import json
import os
import re
import tempfile

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .cloud_context import owner_id, workspace, owner
from . import cloud_tasks

UUID = re.compile(r'^[0-9a-fA-F-]{36}$')
TASK_ID = re.compile(r'^[a-f0-9]{12}$')


def supabase_headers():
    return {'apikey': os.environ['SUPABASE_PUBLISHABLE_KEY']}


async def auth_user(token):
    if not token:
        return None
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(os.environ['SUPABASE_URL'].rstrip('/') + '/auth/v1/user',
                                    headers={**supabase_headers(), 'Authorization': 'Bearer ' + token})
    if not response.is_success: return None
    value = response.json()
    return value if UUID.fullmatch(value.get('id', '')) else None


def cookie(response, access, refresh):
    response.set_cookie('engineering_access', access, httponly=True, secure=True, samesite='strict', path='/', max_age=3600)
    response.set_cookie('engineering_refresh', refresh, httponly=True, secure=True, samesite='strict', path='/api/auth', max_age=30*86400)


class CloudApplication:
    def __init__(self, business): self.business = business

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.business(scope, receive, send)
        request = Request(scope, receive)
        original_send = send
        async def private_send(message):
            if message['type'] == 'http.response.start':
                message['headers'] = [(k,v) for k,v in message.get('headers',[]) if k.lower()!=b'cache-control'] + [(b'cache-control',b'private, no-store')]
            await original_send(message)
        send = private_send
        path = request.url.path
        response = None
        user_token = owner_id.set('')
        with tempfile.TemporaryDirectory(prefix='engineering-') as directory:
            work_token = workspace.set(directory)
            try:
                response = await self.handle(request)
                if response is None:
                    await self.business(scope, receive, send)
                else:
                    await response(scope, receive, send)
            except (ValueError, PermissionError) as exc:
                await JSONResponse({'detail': str(exc)[:240]}, status_code=400)(scope, receive, send)
            except Exception:
                # Avoid leaking storage keys, credentials or provider responses to the browser.
                await JSONResponse({'detail': '云端服务执行失败，请查看任务状态或服务日志'}, status_code=503)(scope, receive, send)
            finally:
                workspace.reset(work_token)
                owner_id.reset(user_token)

    async def handle(self, request):
        path = request.url.path
        if path == '/api/deployment':
            return JSONResponse({'runtime': 'cloud', 'authentication': True})
        if path == '/api/internal/outbox' and request.method == 'POST':
            secret = os.getenv('ENGINEERING_SERVICE_TOKEN', '')
            if len(secret)<32 or not hmac.compare_digest(request.headers.get('Authorization','').encode(),('Bearer '+secret).encode()):
                return JSONResponse({'detail':'unauthorized'},status_code=401)
            def pending():
                import psycopg
                from psycopg.rows import dict_row
                # Explicit administrative outbox read, reachable only with the worker service secret.
                with psycopg.connect(os.environ['SUPABASE_DB_URL'],row_factory=dict_row,connect_timeout=15) as con:
                    return con.execute("SELECT id,owner_id::text FROM engineering.cloud_tasks WHERE state='queued' OR (state='running' AND updated_at::timestamptz < now()-interval '15 minutes') ORDER BY created_at LIMIT 100").fetchall()
            return JSONResponse(await run_in_threadpool(pending))
        if path == '/api/internal/redispatch' and request.method == 'POST':
            secret = os.getenv('ENGINEERING_SERVICE_TOKEN', '')
            if len(secret)<32 or not hmac.compare_digest(request.headers.get('Authorization','').encode(),('Bearer '+secret).encode()):
                return JSONResponse({'detail':'unauthorized'},status_code=401)
            data=await request.json()
            if not UUID.fullmatch(data.get('ownerId','')) or not TASK_ID.fullmatch(data.get('taskId','')):
                return JSONResponse({'detail':'invalid task'},status_code=400)
            owner_id.set(data['ownerId'])
            await run_in_threadpool(cloud_tasks.dispatch,data['taskId'])
            return JSONResponse({'dispatched':True})
        if not path.startswith('/api/'):
            return JSONResponse({'detail': '前端由 Vercel 提供'}, status_code=404)
        internal = path.startswith('/api/internal/tasks/')
        if internal:
            secret = os.getenv('ENGINEERING_SERVICE_TOKEN', '')
            supplied = request.headers.get('Authorization', '')
            user = request.headers.get('X-Engineering-Owner', '')
            if len(secret) < 32 or not hmac.compare_digest(supplied.encode(), ('Bearer ' + secret).encode()) or not UUID.fullmatch(user):
                return JSONResponse({'detail': 'unauthorized'}, status_code=401)
            owner_id.set(user)
            task_id = path.rsplit('/', 1)[-1]
            if request.method != 'POST' or not TASK_ID.fullmatch(task_id):
                return JSONResponse({'detail': '任务不存在'}, status_code=404)
            if os.getenv('ENGINEERING_TASK_PROVIDER') == 'vercel':
                from .cloud_runner import supervised_task
                return await supervised_task(task_id)
            return await self.execute_task(task_id)

        # Same-origin cookies protect previews/downloads as well as API calls.
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            expected = os.environ.get('ENGINEERING_APP_ORIGIN', '').rstrip('/')
            origin = request.headers.get('Origin', '').rstrip('/')
            if not expected or origin != expected:
                return JSONResponse({'detail': '请求来源不匹配'}, status_code=403)
        if path in ('/api/auth/login', '/api/auth/refresh') and request.method == 'POST':
            if path.endswith('login'):
                value = await request.json()
                body = {'email': str(value.get('email', ''))[:320], 'password': str(value.get('password', ''))[:1000]}
                grant = 'password'
            else:
                body = {'refresh_token': request.cookies.get('engineering_refresh', '')}
                grant = 'refresh_token'
            async with httpx.AsyncClient(timeout=15) as client:
                result = await client.post(os.environ['SUPABASE_URL'].rstrip('/') + '/auth/v1/token?grant_type=' + grant,
                                          headers=supabase_headers(), json=body)
            if not result.is_success:
                return JSONResponse({'detail': '登录失败或会话已过期'}, status_code=401)
            data = result.json()
            response = JSONResponse({'authenticated': True})
            cookie(response, data['access_token'], data['refresh_token'])
            return response
        if path == '/api/auth/logout' and request.method == 'POST':
            response = JSONResponse({'authenticated': False})
            response.delete_cookie('engineering_access', path='/')
            response.delete_cookie('engineering_refresh', path='/api/auth')
            return response
        token = request.cookies.get('engineering_access', '')
        user = await auth_user(token)
        if not user: return JSONResponse({'detail': '请登录工程平台'}, status_code=401)
        owner_id.set(user['id'])
        if path == '/api/auth/me':
            return JSONResponse({'id': user['id'], 'email': user.get('email', '')})
        if path in ('/api/uploads/prepare','/api/uploads/complete') and request.method=='POST':
            from . import cloud_uploads
            raw=await request.body()
            if len(raw)>100000:return JSONResponse({'detail':'请求过大'},status_code=413)
            value=json.loads(raw)
            if path.endswith('/prepare'):
                return JSONResponse(await run_in_threadpool(cloud_uploads.prepare,value))
            return await cloud_uploads.complete(value,self.business)
        if path.startswith('/api/cloud/tasks/') and request.method == 'GET':
            task_id = path.rsplit('/', 1)[-1]
            if not TASK_ID.fullmatch(task_id): return JSONResponse({'detail': '任务不存在'}, status_code=404)
            item = await run_in_threadpool(cloud_tasks.task, task_id)
            if not item: return JSONResponse({'detail': '任务不存在'}, status_code=404)
            if item['state'] == 'queued':
                try: await run_in_threadpool(cloud_tasks.dispatch, task_id)
                except (RuntimeError, httpx.HTTPError): pass
            return JSONResponse({'id': task_id, 'state': item['state'], 'result': json.loads(item['result']), 'error': item['error']})
        if path == '/api/cloud/tasks' and request.method == 'GET':
            from .db import connect, rows
            def listing():
                with connect() as con:
                    return rows(con, "SELECT id,project_id,state,error,created_at FROM cloud_tasks WHERE state IN ('queued','running','failed') ORDER BY created_at DESC LIMIT 30")
            return JSONResponse(await run_in_threadpool(listing))
        if request.method == 'POST' and cloud_tasks.LONG_ROUTE.fullmatch(path):
            raw = await request.body()
            if len(raw) > 100_000: return JSONResponse({'detail': '请求过大'}, status_code=413)
            body = json.loads(raw) if raw else {}
            if path == '/api/chat':
                project_id = body.get('project_id', '')
            elif '/projects/' in path:
                project_id = path.split('/')[3]
            else:
                from .db import connect, row
                def part_project():
                    with connect() as con:
                        part = row(con, 'SELECT project_id FROM parts WHERE id=?', (path.split('/')[3],))
                        return part['project_id'] if part else ''
                project_id = await run_in_threadpool(part_project)
            result = await run_in_threadpool(cloud_tasks.enqueue, project_id, 'request', {'path': path, 'body': body})
            return JSONResponse(result, status_code=202)
        return None

    async def execute_task(self, task_id):
        from .cloud_db import raw_connection
        from .cloud_activity import begin, task_context, TaskYield
        item = await run_in_threadpool(cloud_tasks.task, task_id)
        if not item: return JSONResponse({'detail': '任务不存在'}, status_code=404)
        # Advisory lock is session scoped: use direct/session Supabase connection, not transaction pooler.
        con = await run_in_threadpool(raw_connection, 'engineering', True)
        try:
            key = owner() + ':' + item['project_id']
            locked = await run_in_threadpool(lambda: con.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS locked', (key,)).fetchone()['locked'])
            if not locked: return JSONResponse({'detail': '本项目另一个工程任务仍在执行'}, status_code=409)
            item = await run_in_threadpool(cloud_tasks.task, task_id)
            if item['state'] in ('completed', 'failed'):
                return JSONResponse({'state': item['state']})
            if item['state'] == 'running':
                await run_in_threadpool(cloud_tasks.fail, item, '上次执行中断，已保留已有成果。请检查后重新发起任务。')
                return JSONResponse({'state': 'failed'})
            if item.get('slices',0)>=40:
                await run_in_threadpool(cloud_tasks.fail,item,'任务已达到分段执行上限，请缩小图纸范围后重试')
                return JSONResponse({'state':'failed'})
            await run_in_threadpool(cloud_tasks.update, task_id, state='running',slices=item.get('slices',0)+1)
            activity_token=begin(task_id)
            payload = json.loads(item['payload'])
            try:
                if item['operation'] == 'workflow':
                    from .agent import run_batch_workflow
                    result = await run_in_threadpool(run_batch_workflow, payload['job_id'], item['project_id'])
                elif item['operation'] == 'request' and cloud_tasks.LONG_ROUTE.fullmatch(payload['path']):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.business), base_url='http://business') as client:
                        response = await client.post(payload['path'], json=payload['body'])
                    result = response.json()
                    if not response.is_success:
                        raise ValueError(str(result.get('detail', '工程生成失败')))
                else: raise ValueError('未知任务类型')
                await run_in_threadpool(cloud_tasks.update, task_id, state='completed', result=json.dumps(result, ensure_ascii=False))
                return JSONResponse({'state': 'completed'})
            except (TaskYield, BaseExceptionGroup) as exc:
                def leaves(error):
                    if isinstance(error,BaseExceptionGroup):
                        return [leaf for child in error.exceptions for leaf in leaves(child)]
                    return [error]
                errors=leaves(exc)
                # Starlette BaseHTTPMiddleware accompanies BaseException with this
                # specific transport error when its response stream closes early.
                if not any(isinstance(e,TaskYield) for e in errors) or any(
                    not isinstance(e,TaskYield) and not (type(e) is RuntimeError and str(e)=='No response returned.')
                    for e in errors): raise
                await run_in_threadpool(cloud_tasks.update,task_id,state='queued')
                return JSONResponse({'state':'queued','reason':'continuing'})
            except Exception as exc:
                reason = str(exc)[:400] if isinstance(exc, ValueError) else '工程执行失败，请核对模型、文件或云端服务配置'
                await run_in_threadpool(cloud_tasks.fail, item, reason)
                return JSONResponse({'state': 'failed'})
            finally:
                task_context.reset(activity_token)
        finally:
            await run_in_threadpool(con.close)
