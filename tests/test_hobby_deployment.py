import json
import time
from pathlib import Path
from contextlib import contextmanager
import pytest
from starlette.testclient import TestClient
from server import cloud_activity as activity
from server import cloud_storage, native_rpc, native_entry
from server.cloud_context import owner_id, workspace

USER='aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'

def test_completed_activity_replayed_after_budget_yield(monkeypatch,tmp_path):
    from server import db
    records={}
    class Connection:
        def execute(self,sql,values): records[(values[0],values[1])]={'result':values[2]}
    @contextmanager
    def connection(): yield Connection()
    monkeypatch.setattr(db,'connect',connection)
    monkeypatch.setattr(db,'row',lambda con,sql,args:records.get(tuple(args)))
    who=owner_id.set(USER); work=workspace.set(tmp_path); token=activity.begin('task')
    calls=[]
    try:
        assert activity.activity_call('model',lambda x:calls.append(x) or {'n':x},3)=={'n':3}
        activity.task_context.get()['deadline']=time.monotonic()
        assert activity.activity_call('model',lambda _:pytest.fail('replayed model'),3)=={'n':3}
        with pytest.raises(activity.TaskYield): activity.activity_call('next',lambda:None)
        assert calls==[3]
    finally:
        activity.task_context.reset(token); workspace.reset(work); owner_id.reset(who)

def test_native_storage_rpc_and_tenant_isolation(monkeypatch,tmp_path):
    objects={}
    monkeypatch.setenv('ENGINEERING_SERVICE_TOKEN','a'*32)
    monkeypatch.setenv('ENGINEERING_NATIVE_URL','https://native.test')
    monkeypatch.setenv('SUPABASE_URL','https://storage.test')
    monkeypatch.setenv('SUPABASE_SECRET_KEY','test-secret')
    def storage(method,key,content=None):
        if method=='POST': objects[key]=content; return b''
        return objects[key]
    monkeypatch.setattr(cloud_storage,'request',storage)
    client=TestClient(native_entry.app)
    async def perform(body): return native_entry.perform(body)
    monkeypatch.setattr(native_entry,'supervised',perform)
    assert client.post('/execute',json={}).status_code==401
    # The service uses an explicit allowlist; callers cannot invoke arbitrary Python.
    headers={'Authorization':'Bearer '+'a'*32,'X-Engineering-Owner':USER}
    assert client.post('/execute',headers=headers,json={'operation':'os.system'}).status_code==400
    monkeypatch.setitem(native_entry.OPERATIONS,'test_file',('native_entry','test_file'))
    def operation(path,output):
        output.write_bytes(path.read_bytes()+b'-edited'); return output
    monkeypatch.setattr(native_entry,'test_file',operation,raising=False)
    monkeypatch.setattr(native_rpc.httpx,'post',lambda url,**kw:client.post('/execute',headers=kw['headers'],json=kw['json']))
    who=owner_id.set(USER); work=workspace.set(tmp_path)
    try:
        path=cloud_storage.root()/'original.dxf';path.write_bytes(b'CAD')
        result=native_rpc.call('test_file',(path,cloud_storage.root()/'out'/'edited.dxf'),{})
        assert result.read_bytes()==b'CAD-edited'
        key=next(iter(objects))
        other={**headers,'X-Engineering-Owner':'bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb'}
        response=client.post('/execute',headers=other,json={'operation':'test_file','input':'object://'+key})
        assert response.status_code==422
    finally: workspace.reset(work); owner_id.reset(who)


def test_engineering_storage_proxy_uses_service_token(monkeypatch, tmp_path):
    import httpx
    calls = []
    monkeypatch.setenv('ENGINEERING_SERVICE_TOKEN', 's' * 32)
    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return httpx.Response(200, content=b'payload', request=httpx.Request(method, url))
    monkeypatch.setattr(cloud_storage.httpx, 'request', request)
    who=owner_id.set(USER); work=workspace.set(tmp_path)
    proxy=cloud_storage.storage_proxy.set('https://agent.example/api/internal/native-storage')
    try:
        assert cloud_storage.request('GET', USER + '/rpc/input.json') == b'payload'
        method, url, kwargs = calls[0]
        assert method == 'GET' and url.endswith('/api/internal/native-storage')
        assert kwargs['params']['key'] == USER + '/rpc/input.json'
        assert kwargs['headers']['Authorization'] == 'Bearer ' + 's' * 32
        assert kwargs['headers']['X-Engineering-Owner'] == USER
    finally:
        cloud_storage.storage_proxy.reset(proxy); workspace.reset(work); owner_id.reset(who)


def test_pdf_page_falls_back_locally_but_cad_remains_strict(monkeypatch):
    monkeypatch.setenv('ENGINEERING_NATIVE_URL','https://native.test')
    monkeypatch.delenv('ENGINEERING_SERVICE_ROLE',raising=False)
    monkeypatch.setattr(native_rpc,'call',lambda *args: (_ for _ in ()).throw(ValueError('remote unavailable')))

    @native_rpc.native('pdf_page')
    def page(): return {'tokens':[{'text':'42'}],'warnings':[]}
    result=page()
    assert result['tokens'][0]['text']=='42'
    assert 'local PDF text fallback' in result['warnings'][0]

    @native_rpc.native('drawing')
    def drawing(): return 'invented'
    with pytest.raises(ValueError,match='remote unavailable'):
        drawing()


def test_native_health_reports_storage_configuration(monkeypatch):
    monkeypatch.delenv('SUPABASE_URL',raising=False)
    monkeypatch.delenv('SUPABASE_SECRET_KEY',raising=False)
    response=TestClient(native_entry.app).get('/health')
    assert response.json()['status']=='misconfigured'
    assert set(response.json()['missing'])=={'SUPABASE_URL','SUPABASE_SECRET_KEY'}


def test_native_job_failure_is_specific_and_traceable(monkeypatch):
    monkeypatch.setenv('ENGINEERING_SERVICE_TOKEN','a'*32)
    monkeypatch.setenv('SUPABASE_URL','https://storage.test')
    monkeypatch.setenv('SUPABASE_SECRET_KEY','test-secret')
    async def fail(_):
        raise native_entry.NativeJobError('TypeError', '尺寸数据无法转换', 'private traceback')
    monkeypatch.setattr(native_entry,'supervised',fail)
    response=TestClient(native_entry.app).post('/execute',headers={
        'Authorization':'Bearer '+'a'*32,'X-Engineering-Owner':USER},
        json={'operation':'drawing','input':'object://input'})
    assert response.status_code==422
    body=response.json()
    assert body['code']=='NATIVE_OPERATION_FAILED'
    assert 'TypeError' in body['detail'] and '尺寸数据无法转换' in body['detail']
    assert len(body['reference'])==12

def test_durable_model_preserves_tool_call_ids(monkeypatch,tmp_path):
    from langchain_core.messages import AIMessage,HumanMessage
    from langchain_core.outputs import ChatResult,ChatGeneration
    from langchain_openai import ChatOpenAI
    from server.cloud_model import DurableChatOpenAI
    saved=[]
    def memo(name,fn,*args):
        if not saved: saved.append(fn(*args))
        return saved[0]
    monkeypatch.setattr('server.cloud_model.activity_call',memo)
    monkeypatch.setattr(ChatOpenAI,'_generate',lambda *a,**k:ChatResult(generations=[ChatGeneration(message=AIMessage(content='',tool_calls=[{'id':'call-1','name':'inspect','args':{}}]))]))
    token=activity.begin('task')
    try:
        model=DurableChatOpenAI(api_key='test',model='test')
        first=model.invoke([HumanMessage(content='inspect')])
        second=model.invoke([HumanMessage(content='inspect')])
        assert first.tool_calls==second.tool_calls
        assert first.tool_calls[0]['id']=='call-1'
    finally: activity.task_context.reset(token)


def test_task_budget_yield_crosses_asgi_middleware(monkeypatch):
    from fastapi import FastAPI
    from server.cloud_app import CloudApplication
    from server import cloud_tasks
    item={'id':'abcdef123456','project_id':'p','state':'queued','operation':'request',
          'payload':json.dumps({'path':'/api/chat','body':{}})}
    states=[]
    class Lock:
        def execute(self,*_): return self
        def fetchone(self): return {'locked':True}
        def close(self): pass
    monkeypatch.setattr('server.cloud_db.raw_connection',lambda *_:Lock())
    monkeypatch.setattr(cloud_tasks,'task',lambda _:item)
    monkeypatch.setattr(cloud_tasks,'update',lambda _,**fields:states.append(fields))
    monkeypatch.setenv('ENGINEERING_SERVICE_TOKEN','x'*32)
    monkeypatch.delenv('ENGINEERING_TASK_PROVIDER',raising=False)
    business=FastAPI()
    @business.middleware('http')
    async def passthrough(request,call_next): return await call_next(request)
    @business.post('/api/chat')
    def generate(): raise activity.TaskYield()
    result=TestClient(CloudApplication(business)).post('/api/internal/tasks/abcdef123456',headers={
        'Authorization':'Bearer '+'x'*32,'X-Engineering-Owner':USER})
    assert result.json()['state']=='queued'
    assert states[-1]['state']=='queued'


def test_deepagent_resumes_cached_model_and_tool_sequence(monkeypatch,tmp_path):
    from server import db,agent
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatResult,ChatGeneration
    from langchain_core.tools import tool
    records={}; model_calls=[]; commits=[]
    class Connection:
        def execute(self,sql,values): records[tuple(values[:2])]={'result':values[2]}
    @contextmanager
    def connection(): yield Connection()
    monkeypatch.setattr(db,'connect',connection)
    monkeypatch.setattr(db,'row',lambda con,sql,args:records.get(tuple(args)))
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-only')
    @tool
    def inspect() -> str:
        """Inspect the current work."""
        return 'ready'
    @tool
    def commit() -> str:
        """Commit the inspected work."""
        commits.append(True); return 'saved'
    def generate(*args,**kwargs):
        index=len(model_calls);model_calls.append(index)
        name=('inspect','commit',None)[index]
        if index==0: activity.task_context.get()['deadline']=time.monotonic()
        message=AIMessage(content='done' if name is None else '',tool_calls=[] if name is None else [{'id':f'call-{index}','name':name,'args':{}}])
        return ChatResult(generations=[ChatGeneration(message=message)])
    monkeypatch.setattr(ChatOpenAI,'_generate',generate)
    who=owner_id.set(USER);work=workspace.set(tmp_path);token=activity.begin('task')
    try:
        with pytest.raises(activity.TaskYield): agent._invoke_specialist('test','Inspect, then commit.',[inspect,commit],'go')
        activity.task_context.get()['deadline']=time.monotonic()+210
        result=agent._invoke_specialist('test','Inspect, then commit.',[inspect,commit],'go')
        assert result['messages'][-1].content=='done'
        assert len(model_calls)==3 and commits==[True]
    finally: activity.task_context.reset(token);workspace.reset(work);owner_id.reset(who)
