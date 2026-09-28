import hashlib
import hmac
import time
import pytest
from starlette.requests import Request
from starlette.applications import Starlette
from starlette.testclient import TestClient
from server.cloud_app import CloudApplication
from server import cloud_guard


def test_proof_binds_path_and_rejects_unsigned(monkeypatch):
    secret = 'x'*32
    monkeypatch.setenv('ENGINEERING_SERVICE_TOKEN', secret)
    stamp = str(int(time.time()))
    proof = hmac.new(secret.encode(), f'{stamp}\nGET\n/api/auth/me\n127.0.0.1'.encode(), hashlib.sha256).hexdigest()
    scope = {'type':'http','method':'GET','path':'/api/auth/me','headers':[(b'x-engineering-time',stamp.encode()),(b'x-engineering-client',b'127.0.0.1'),(b'x-engineering-proof',proof.encode())]}
    assert len(cloud_guard.client_key(Request(scope))) == 64
    with pytest.raises(PermissionError):
        cloud_guard.client_key(Request({**scope,'path':'/api/projects'}))
    with pytest.raises(PermissionError):
        cloud_guard.client_key(Request({**scope,'headers':[]}))


def test_existing_session_is_reused_and_password_endpoint_removed(monkeypatch):
    monkeypatch.setenv('ENGINEERING_APP_ORIGIN','https://test.example')
    monkeypatch.setattr(cloud_guard,'client_key',lambda _: 'ip')
    monkeypatch.setattr(cloud_guard,'limit',lambda *_: None)
    async def auth(_): return {'id':'existing-owner'}
    monkeypatch.setattr('server.cloud_app.auth_user',auth)
    client=TestClient(CloudApplication(Starlette()),base_url='https://test.example')
    headers={'origin':'https://test.example'}
    assert client.post('/api/auth/anonymous',headers=headers).json()['id']=='existing-owner'
    assert client.post('/api/auth/login',headers=headers).status_code==410
    assert client.post('/api/auth/anonymous').status_code==403


def test_anonymous_signup_sets_private_cookies(monkeypatch):
    import httpx
    monkeypatch.setenv('ENGINEERING_APP_ORIGIN','https://test.example')
    monkeypatch.setenv('SUPABASE_URL','https://supabase.example')
    monkeypatch.setenv('SUPABASE_PUBLISHABLE_KEY','public-test')
    monkeypatch.setattr(cloud_guard,'client_key',lambda _: 'ip')
    monkeypatch.setattr(cloud_guard,'limit',lambda *_: None)
    async def auth(_): return None
    monkeypatch.setattr('server.cloud_app.auth_user',auth)
    class Provider:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,url,**kwargs):
            assert url.endswith('/auth/v1/signup')
            assert kwargs['json']=={}
            return httpx.Response(200,json={'user':{'id':'guest'},'access_token':'access','refresh_token':'refresh'})
    monkeypatch.setattr('server.cloud_app.httpx.AsyncClient',Provider)
    client=TestClient(CloudApplication(Starlette()),base_url='https://test.example')
    result=client.post('/api/auth/anonymous',headers={'origin':'https://test.example'})
    assert result.status_code==200
    assert all('HttpOnly' in v and 'Secure' in v for v in result.headers.get_list('set-cookie'))
