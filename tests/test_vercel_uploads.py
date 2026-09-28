import time
import httpx
import pytest
from server import cloud_uploads as uploads
from server.cloud_context import owner_id

USER='aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'

@pytest.fixture(autouse=True)
def context(monkeypatch):
    token=owner_id.set(USER)
    monkeypatch.setenv('ENGINEERING_SERVICE_TOKEN','x'*40)
    monkeypatch.setenv('SUPABASE_URL','https://test.supabase.co')
    monkeypatch.setenv('SUPABASE_SECRET_KEY','test-key')
    yield
    owner_id.reset(token)

def test_tickets_reject_tampering_expiry_and_other_user():
    data={'owner':USER,'expires':time.time()+100}
    value=uploads.ticket(data)
    assert uploads.verify(value)==data
    with pytest.raises(PermissionError): uploads.verify(value[:-2]+'aa')
    with pytest.raises(PermissionError): uploads.verify(uploads.ticket({**data,'expires':0}))
    with pytest.raises(PermissionError): uploads.verify(uploads.ticket({**data,'owner':'other'}))

@pytest.mark.parametrize('path',['/object/sign/bucket/file?token=x','/storage/v1/object/sign/bucket/file?token=x','https://test.supabase.co/storage/v1/object/sign/bucket/file?token=x'])
def test_signed_paths_do_not_duplicate_storage_prefix(monkeypatch,path):
    monkeypatch.setattr(httpx,'post',lambda *a,**k:httpx.Response(200,json={'signedURL':path},request=httpx.Request('POST',a[0])))
    assert uploads.signed_url(USER+'/file')=='https://test.supabase.co/storage/v1/object/sign/bucket/file?token=x'
    with pytest.raises(PermissionError):uploads.signed_url('another/file')

def test_prepare_limits_target_size_and_extension(monkeypatch):
    monkeypatch.setattr(uploads,'signed_url',lambda *a:'https://test.supabase.co/upload')
    base={'target':'/api/projects/upload','name':'drawing.pdf','size':100}
    result=uploads.prepare(base)
    assert uploads.verify(result['ticket'])['key'].startswith(USER+'/incoming/')
    for change in ({'target':'/api/internal/tasks/123'},{'size':31*1024*1024},{'name':'app.exe'}):
        with pytest.raises(ValueError):uploads.prepare({**base,**change})

def test_large_cad_json_is_bound_to_cad_save_route(monkeypatch):
    monkeypatch.setattr(uploads,'signed_url',lambda *a:'https://test.supabase.co/upload')
    value=uploads.prepare({'target':'/api/parts/abcdef/cad/studio/123abc/save','name':'edit.json','size':2000000})
    assert uploads.verify(value['ticket'])['json'] is True
    with pytest.raises(ValueError):uploads.prepare({'target':'/api/projects/upload','name':'edit.json','size':100})

def test_vercel_dispatch_preserves_run_id(monkeypatch):
    from server import cloud_tasks
    writes=[]
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,sql,params):writes.append((sql,params))
    monkeypatch.setenv('ENGINEERING_TASK_PROVIDER','vercel')
    monkeypatch.setenv('ENGINEERING_ORCHESTRATOR_URL','https://internal.test/base/')
    monkeypatch.setattr(cloud_tasks,'connect',Connection)
    monkeypatch.setattr(cloud_tasks,'row',lambda *args:{'state':'queued','workflow_run_id':'run_old'})
    def dispatch(url,**kwargs):
        assert url=='https://internal.test/base/api/cloud/dispatch'
        assert kwargs['json']['previousRunId']=='run_old'
        assert kwargs['json']['ownerId']==USER
        return httpx.Response(202,json={'runId':'run_new'},request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx,'post',dispatch)
    cloud_tasks.dispatch('abcdef123456')
    assert writes[0][1][0]=='run_new'

def test_direct_upload_complete_preserves_business_validation(tmp_path,monkeypatch):
    import asyncio
    import json
    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.responses import JSONResponse
    path=tmp_path/'cad.json';path.write_text('{"dxf":"test","revision":"r1"}')
    monkeypatch.setattr(uploads,'materialize',lambda _:str(path))
    async def save(request):
        body=await request.json()
        return JSONResponse({'revision':body['revision']},status_code=409)
    app=Starlette(routes=[Route('/api/parts/a/cad/studio/b/save',save,methods=['POST'])])
    value=uploads.ticket({'owner':USER,'expires':time.time()+100,'key':USER+'/incoming/a.json',
        'target':'/api/parts/a/cad/studio/b/save','size':path.stat().st_size,'json':True})
    result=asyncio.run(uploads.complete({'ticket':value},app))
    assert result.status_code==409
    assert json.loads(result.body)=={'revision':'r1'}

def test_supervisor_timeout_kills_process_and_records_failure(monkeypatch):
    import asyncio
    from server import cloud_runner
    events=[]
    class Process:
        returncode=None
        pid=1234
        async def wait(self):events.append('wait')
        def kill(self):events.append('kill')
    async def spawn(*a,**k):return Process()
    async def timeout(coro,seconds):coro.close();raise TimeoutError()
    async def interrupted(task,reason):events.append('failed')
    monkeypatch.setattr(asyncio,'create_subprocess_exec',spawn)
    monkeypatch.setattr(asyncio,'wait_for',timeout)
    monkeypatch.setattr(cloud_runner,'mark_interrupted',interrupted)
    if cloud_runner.os.name!='nt':monkeypatch.setattr(cloud_runner.os,'killpg',lambda *args:events.append('kill'))
    response=asyncio.run(cloud_runner.supervised_task('abcdef123456'))
    assert response.status_code==200
    assert events==['kill','wait','failed']
