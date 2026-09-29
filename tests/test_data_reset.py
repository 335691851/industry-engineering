import json

from server import cloud_storage, data_reset, db
from server.cloud_context import owner_id


USER = 'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'


def test_local_business_reset_removes_records_and_files(tmp_path, monkeypatch):
    monkeypatch.delenv('VERCEL', raising=False)
    monkeypatch.setenv('ENGINEERING_RUNTIME', 'local')
    monkeypatch.setattr(db, 'DATA', tmp_path)
    monkeypatch.setattr(db, 'FILES', tmp_path / 'files')
    monkeypatch.setattr(db, 'DB', tmp_path / 'engineering.db')
    db.init()
    db.FILES.mkdir(exist_ok=True)
    (db.FILES / 'drawing.pdf').write_bytes(b'%PDF')
    with db.connect() as con:
        con.execute('INSERT INTO projects (id,name,drawing_no,source_path,analysis,created_at) VALUES (?,?,?,?,?,?)',
                    ('p', '装配体', 'A-01', 'source.pdf', '{}', db.now()))
        con.execute('INSERT INTO parts (id,project_id,name,updated_at) VALUES (?,?,?,?)',
                    ('part', 'p', '轴', db.now()))
        con.execute('INSERT INTO jobs (id,project_id,status,total,created_at,updated_at) VALUES (?,?,?,?,?,?)',
                    ('job', 'p', '完成', 1, db.now(), db.now()))
        con.execute('INSERT INTO engineering_memory VALUES (?,?,?,?,?,?,?)',
                    ('memory', 'drawing_example', '历史图纸', json.dumps({}), 'upload', '', db.now()))

    result = data_reset.clear_business_data()

    assert result == {'projects': 1, 'parts': 1, 'tasks': 1, 'memory': 1, 'files': 1, 'cleared': True}
    with db.connect() as con:
        assert con.execute('SELECT count(*) AS count FROM projects').fetchone()['count'] == 0
        assert con.execute('SELECT count(*) AS count FROM engineering_memory').fetchone()['count'] == 0
    assert db.FILES.is_dir() and not list(db.FILES.iterdir())


def test_cloud_storage_reset_is_recursive_and_tenant_scoped(monkeypatch):
    deleted = []
    monkeypatch.setenv('SUPABASE_URL', 'https://storage.test')
    monkeypatch.setenv('SUPABASE_SECRET_KEY', 'secret')

    class Response:
        def __init__(self, value): self.value = value; self.status_code = 200; self.is_success = True
        def json(self): return self.value

    def listing(url, **kwargs):
        prefix = kwargs['json']['prefix']
        if prefix == USER:
            return Response([{'name': 'project', 'id': None, 'metadata': None}])
        if prefix == USER + '/project':
            return Response([{'name': 'drawing.pdf', 'id': 'object-id', 'metadata': {}}])
        return Response([])

    def request(method, url, **kwargs):
        assert method == 'DELETE'
        deleted.extend(kwargs['json']['prefixes'])
        return Response([])

    monkeypatch.setattr(cloud_storage.httpx, 'post', listing)
    monkeypatch.setattr(cloud_storage.httpx, 'request', request)
    token = owner_id.set(USER)
    try:
        assert cloud_storage.clear_owner_objects() == 1
    finally:
        owner_id.reset(token)
    assert deleted == [USER + '/project/drawing.pdf']
