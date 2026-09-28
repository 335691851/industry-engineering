import json
from pathlib import Path
import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from server.cloud_context import owner_id, workspace, owner
from server import cloud_storage, cloud_tasks
from server.cloud_app import CloudApplication
from server.cloud_db import translate

USER = 'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'


def test_query_translation_preserves_parameter_data():
    assert translate('INSERT INTO messages VALUES (?,?,?,?,?,?,?)').startswith('INSERT INTO messages (id,project_id,role,mode,content,part_id,created_at)')
    assert translate('UPDATE parts SET drawing_pdf="",status=? WHERE id=?') == "UPDATE parts SET drawing_pdf='',status=%s WHERE id=%s"
    assert translate("SELECT '?' AS x WHERE id=?") == "SELECT '?' AS x WHERE id=%s"


def test_private_objects_survive_cold_start_and_reject_cross_tenant(tmp_path, monkeypatch):
    objects = {}
    def storage(method, key, content=None):
        if method == 'POST': objects[key] = content; return b''
        return objects[key]
    monkeypatch.setattr(cloud_storage, 'request', storage)
    who = owner_id.set(USER)
    work = workspace.set(tmp_path / 'first')
    try:
        file = cloud_storage.root() / 'project' / 'drawing.pdf'
        file.parent.mkdir(); file.write_bytes(b'%PDF-test')
        encoded = cloud_storage.transform(json.dumps({'source': str(file)}))
        assert 'object://' + USER in encoded
        workspace.set(tmp_path / 'cold-start')
        decoded = json.loads(cloud_storage.transform(encoded, downloading=True))
        assert Path(decoded['source']).read_bytes() == b'%PDF-test'
        assert str(tmp_path / 'cold-start') in decoded['source']
        with pytest.raises(PermissionError):
            cloud_storage.materialize('object://bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb/project/drawing.pdf')
        with pytest.raises(PermissionError):
            cloud_storage.materialize('object://' + USER + '/../escape')
    finally:
        workspace.reset(work); owner_id.reset(who)


def test_cloud_auth_csrf_tenant_context_and_async_generation(monkeypatch):
    monkeypatch.setattr('server.cloud_guard.client_key', lambda request: 'test')
    monkeypatch.setattr('server.cloud_guard.limit', lambda *args: None)
    async def auth(token): return {'id': USER} if token == 'valid' else None
    monkeypatch.setattr('server.cloud_app.auth_user', auth)
    monkeypatch.setenv('ENGINEERING_APP_ORIGIN', 'https://app.example.com')
    received = []
    def enqueue(project, operation, payload):
        received.append((owner(), project, operation, payload))
        return {'cloud_task_id':'abcdef123456', 'state':'queued'}
    monkeypatch.setattr(cloud_tasks, 'enqueue', enqueue)
    async def project(request): return JSONResponse({'owner': owner(), 'workspace': str(workspace.get())})
    app = CloudApplication(Starlette(routes=[Route('/api/projects/x', project)]))
    client = TestClient(app, base_url='https://app.example.com')
    assert client.get('/api/projects/x').status_code == 401
    client.cookies.set('engineering_access', 'valid')
    result = client.get('/api/projects/x')
    assert result.headers['cache-control'] == 'private, no-store'
    assert result.json()['owner'] == USER
    assert not Path(result.json()['workspace']).exists(), 'Temporary files must be cleaned after response'
    assert owner_id.get() == ''
    assert client.post('/api/chat', json={'project_id':'x'}).status_code == 403
    result = client.post('/api/chat', json={'project_id':'x','message':'test'}, headers={'Origin':'https://app.example.com'})
    assert result.status_code == 202
    assert received[0][0] == USER and received[0][1] == 'x'
    assert client.post('/api/internal/tasks/abcdef123456').status_code == 401


def test_cloud_dispatch_keeps_outbox_on_network_failure(monkeypatch):
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def execute(self, sql, params): writes.append((sql, params))
    writes=[]
    monkeypatch.setattr(cloud_tasks,'connect',Connection)
    monkeypatch.setattr(cloud_tasks,'row',lambda *_:{'id':'p'})
    monkeypatch.setattr(cloud_tasks,'dispatch',lambda _: (_ for _ in ()).throw(RuntimeError('offline')))
    result=cloud_tasks.enqueue('p','request',{'path':'/api/chat','body':{}})
    assert result['state']=='queued'
    assert writes and 'INSERT INTO cloud_tasks' in writes[0][0]


@pytest.mark.parametrize('state,locked,expected,calls',[
    ('queued',True,200,1),('completed',True,200,0),
    ('running',True,200,0),('queued',False,409,0),
])
def test_task_lock_replay_and_interrupted_execution(monkeypatch,state,locked,expected,calls):
    item={'id':'abcdef123456','project_id':'p','state':state,'operation':'request',
          'payload':json.dumps({'path':'/api/chat','body':{'project_id':'p'}})}
    executed=[]; updates=[]; closed=[]
    class Lock:
        def execute(self,*_): return self
        def fetchone(self): return {'locked':locked}
        def close(self): closed.append(True)
    monkeypatch.setattr('server.cloud_db.raw_connection',lambda *_:Lock())
    monkeypatch.setattr(cloud_tasks,'task',lambda _:item.copy())
    monkeypatch.setattr(cloud_tasks,'update',lambda _id,**kw:updates.append(kw))
    monkeypatch.setattr(cloud_tasks,'fail',lambda _item,reason:updates.append({'state':'failed'}))
    monkeypatch.setenv('ENGINEERING_SERVICE_TOKEN','x'*32)
    async def generate(request):
        executed.append(owner())
        return JSONResponse({'content':'generated'})
    client=TestClient(CloudApplication(Starlette(routes=[Route('/api/chat',generate,methods=['POST'])])))
    response=client.post('/api/internal/tasks/abcdef123456',headers={
        'Authorization':'Bearer '+'x'*32,'X-Engineering-Owner':USER})
    assert response.status_code==expected
    assert len(executed)==calls and closed==[True]
    if calls: assert updates[-1]['state']=='completed' and executed==[USER]
    if state=='running': assert updates==[{'state':'failed'}]
