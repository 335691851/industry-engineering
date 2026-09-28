"""Direct private-storage uploads, bound to the authenticated user and business endpoint."""
import base64
import hashlib
import hmac
import json
import os
import re
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlparse
import httpx
from .cloud_context import owner
from .cloud_storage import materialize, PREFIX

TARGET = re.compile(r'^/api/(?:projects/upload|parts/[a-f0-9]+/(?:resources|cad/(?:studio/)?upload)|memory)$')
CAD_JSON = re.compile(r'^/api/parts/[a-f0-9]+/cad/studio/[a-f0-9]+/(?:baseline|save)$')


def headers():
    secret=os.environ['SUPABASE_SECRET_KEY']
    return {'apikey':secret,**({'Authorization':'Bearer '+secret} if secret.startswith('eyJ') else {})}


def storage_url(path):
    return os.environ['SUPABASE_URL'].rstrip('/')+'/storage/v1/'+path


def signed_url(key,upload=False):
    if not key.startswith(owner()+'/'):raise PermissionError('文件不属于当前用户')
    bucket=quote(os.getenv('SUPABASE_STORAGE_BUCKET','engineering-private'),safe='')
    route=('object/upload/sign/' if upload else 'object/sign/')+bucket+'/'+quote(key,safe='/')
    response=httpx.post(storage_url(route),headers=headers(),json={} if upload else {'expiresIn':300},timeout=20)
    response.raise_for_status()
    value=response.json(); url=value.get('url') or value.get('signedURL')
    if not url:raise RuntimeError('文件签名失败')
    if url.startswith('https://'):
        result=url
    elif url.startswith('/storage/v1/'):
        result=os.environ['SUPABASE_URL'].rstrip('/')+url
    else:
        result=storage_url(url.lstrip('/'))
    if urlparse(result).netloc != urlparse(os.environ['SUPABASE_URL']).netloc:
        raise ValueError('文件签名来源不匹配')
    return result


def ticket(data):
    raw=base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
    signature=hmac.new(os.environ['ENGINEERING_SERVICE_TOKEN'].encode(),raw.encode(),hashlib.sha256).hexdigest()
    return raw+'.'+signature


def verify(value):
    raw,signature=value.rsplit('.',1)
    expected=hmac.new(os.environ['ENGINEERING_SERVICE_TOKEN'].encode(),raw.encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature,expected):raise PermissionError('上传凭证无效')
    data=json.loads(base64.urlsafe_b64decode(raw))
    if data['owner']!=owner() or data['expires']<time.time():raise PermissionError('上传凭证已过期或不属于当前用户')
    return data


def prepare(value):
    target=value.get('target','')
    structured=bool(CAD_JSON.fullmatch(target))
    if not TARGET.fullmatch(target) and not structured:raise ValueError('不支持的上传目标')
    name=Path(value.get('name','')).name
    allowed={'.json'} if structured else {'.pdf','.dwg','.dxf','.xlsx','.txt','.md'}
    if Path(name).suffix.lower() not in allowed:raise ValueError('不支持的文件格式')
    size=int(value.get('size',0))
    if size<=0 or size>30*1024*1024:raise ValueError('文件大小必须在 30 MB 以内')
    key=f'{owner()}/incoming/{uuid.uuid4().hex}{Path(name).suffix.lower()}'
    data={'owner':owner(),'key':key,'target':target,'name':name,'size':size,'json':structured,'expires':time.time()+7200}
    return {'url':signed_url(key,True),'ticket':ticket(data)}


async def complete(value,business):
    from starlette.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool
    data=verify(value['ticket'])
    fields=value.get('fields',{})
    if not isinstance(fields,dict) or any(not isinstance(v,str) or len(v)>20000 for v in fields.values()):
        raise ValueError('上传表单无效')
    path=Path(await run_in_threadpool(materialize,PREFIX+data['key']))
    if path.stat().st_size!=data['size']:raise ValueError('上传文件大小不匹配')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=business),base_url='http://business') as client:
        if data.get('json'):
            result=await client.post(data['target'],json=json.loads(path.read_text(encoding='utf-8')))
        else:
            with path.open('rb') as file:
                result=await client.post(data['target'],data=fields,files={'file':(data['name'],file)})
    # Source remains private. Unused incoming objects can be removed by a retention policy.
    return JSONResponse(result.json(),status_code=result.status_code)
