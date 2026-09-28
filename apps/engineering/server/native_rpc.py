"""Allowlisted native activities. Large inputs/results travel through private Storage."""
import functools
import json
import os
import uuid
from pathlib import Path
import httpx
from .cloud_context import owner


def remote_enabled():
    return bool(os.getenv('ENGINEERING_NATIVE_URL')) and os.getenv('ENGINEERING_SERVICE_ROLE') != 'engineering'


def call(operation, args, kwargs):
    from .cloud_storage import transform, publish, materialize, root
    # Output paths may not yet exist: encode them as tenant-relative path descriptors.
    def encode(v):
        if isinstance(v, Path):
            if v.is_file(): return {'$file':publish(v)}
            return {'$output':v.resolve().relative_to(root().resolve()).as_posix()}
        if isinstance(v,dict): return {k:encode(x) for k,x in v.items()}
        if isinstance(v,(tuple,list)): return [encode(x) for x in v]
        return transform(v)
    payload=root()/'rpc'/f'{uuid.uuid4().hex}-input.json'
    payload.parent.mkdir(parents=True,exist_ok=True)
    payload.write_text(json.dumps({'args':encode(args),'kwargs':encode(kwargs)},ensure_ascii=False),encoding='utf-8')
    headers={'Authorization':'Bearer '+os.environ['ENGINEERING_SERVICE_TOKEN'],'X-Engineering-Owner':owner()}
    if os.getenv('ENGINEERING_NATIVE_BYPASS'): headers['x-vercel-protection-bypass']=os.environ['ENGINEERING_NATIVE_BYPASS']
    response=httpx.post(os.environ['ENGINEERING_NATIVE_URL'].rstrip('/')+'/execute',
        headers=headers,json={'operation':operation,'input':publish(payload)},timeout=115)
    if not response.is_success:
        try:
            failure = response.json()
        except (ValueError, TypeError):
            failure = {}
        code = str(failure.get('code', 'NATIVE_OPERATION_FAILED'))[:64]
        detail = str(failure.get('detail', '请查看工程服务日志'))[:240]
        raise ValueError(f'工程文件处理失败（{operation}，HTTP {response.status_code}，{code}）：{detail}')
    result=json.loads(Path(materialize(response.json()['output'])).read_text(encoding='utf-8'))
    def decode(v):
        if isinstance(v,dict) and set(v)=={'$file'}: return Path(materialize(v['$file']))
        if isinstance(v,dict): return {k:decode(x) for k,x in v.items()}
        if isinstance(v,list): return [decode(x) for x in v]
        return transform(v,downloading=True)
    return decode(result)


def native(operation):
    def decorate(fn):
        @functools.wraps(fn)
        def wrapped(*args,**kwargs):
            if remote_enabled():
                from .cloud_activity import activity_call
                try:
                    return activity_call('native_'+operation,call,operation,args,kwargs)
                except (ValueError, OSError) as exc:
                    # PDF text extraction is pure, bounded and already installed in the
                    # Agent image. Keep vector PDFs usable when the optional OCR service
                    # is unhealthy. Native CAD operations must never silently degrade.
                    if operation != 'pdf_page':
                        raise
                    result = fn(*args, **kwargs)
                    result.setdefault('warnings', []).append(
                        f'remote OCR unavailable ({type(exc).__name__}); local PDF text fallback used')
                    return result
            return fn(*args,**kwargs)
        return wrapped
    return decorate
