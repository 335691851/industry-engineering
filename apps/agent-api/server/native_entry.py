"""Separate engineering container. No model key or business database required."""
import hmac
import json
import os
import tempfile
import uuid
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from .cloud_context import owner_id, workspace

app=FastAPI()

REQUIRED_STORAGE=('SUPABASE_URL','SUPABASE_SECRET_KEY')

async def supervised(body):
    import asyncio,sys,signal
    folder=Path(workspace.get())
    source=folder/'job.json'; result=folder/'job-result.json'
    source.write_text(json.dumps(body),encoding='utf-8')
    options={'start_new_session':True} if os.name!='nt' else {}
    process=await asyncio.create_subprocess_exec(sys.executable,'-m','server.native_job',
        owner_id.get(),str(folder),str(source),str(result),**options)
    try:
        await asyncio.wait_for(process.wait(),100)
    except (TimeoutError,asyncio.CancelledError):
        if process.returncode is None:
            if os.name!='nt': os.killpg(process.pid,signal.SIGKILL)
            else: process.kill()
            await process.wait()
        raise ValueError('单个工程文件任务超时，请拆分图纸')
    if process.returncode or not result.is_file(): raise ValueError('工程子进程失败')
    return json.loads(result.read_text(encoding='utf-8'))

# Resolve lazily: importing a health route must not load OCR/OpenCascade.
OPERATIONS={
    'drawing':('drawing','export_drawing'),
    'process_pdf':('drawing','export_process_pdf'),
    'process_xlsx':('drawing','export_process_xlsx'),
    'pdf_page':('evidence','extract_page'),
    'cad_pdf':('cad_import','cad_to_pdf'),
    'cad_artifacts':('cad_artifacts','render_artifacts'),
    'dwg_read':('native_entry','dwg_read'),
    'dwg_write':('dwg_converter','convert_dwg'),
}

def dwg_read(path):
    from .dwg_converter import read_dwg
    output=path.with_suffix('.import.dxf')
    read_dwg(path).saveas(output)
    return output

def perform(body):
    import importlib
    from .cloud_storage import root, materialize, publish, transform
    source=Path(materialize(body['input']))
    if source.stat().st_size>32*1024*1024: raise ValueError('工程任务输入过大')
    payload=json.loads(source.read_text(encoding='utf-8'))
    def decode(v):
        if isinstance(v,dict) and set(v)=={'$file'}: return Path(materialize(v['$file']))
        if isinstance(v,dict) and set(v)=={'$output'}:
            path=(root()/v['$output']).resolve()
            if not path.is_relative_to(root().resolve()): raise PermissionError('输出路径无效')
            path.parent.mkdir(parents=True,exist_ok=True)
            return path
        if isinstance(v,dict): return {k:decode(x) for k,x in v.items()}
        if isinstance(v,list): return [decode(x) for x in v]
        return transform(v,downloading=True)
    module,name=OPERATIONS[body['operation']]
    fn=getattr(importlib.import_module('server.'+module),name)
    result=fn(*decode(payload['args']),**decode(payload['kwargs']))
    def encode(v):
        if isinstance(v,Path): return {'$file':publish(v)}
        if isinstance(v,dict): return {k:encode(x) for k,x in v.items()}
        if isinstance(v,(list,tuple)): return [encode(x) for x in v]
        return transform(v)
    target=root()/'rpc'/f'{uuid.uuid4().hex}-output.json'
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(encode(result),ensure_ascii=False),encoding='utf-8')
    return {'output':publish(target)}

@app.get('/health')
def health():
    missing=[name for name in REQUIRED_STORAGE if not os.getenv(name)]
    return {'service':'engineering','status':'ok' if not missing else 'misconfigured',
            'storage_configured':not missing,'missing':missing}

@app.post('/execute')
async def execute(request:Request):
    secret=os.getenv('ENGINEERING_SERVICE_TOKEN','')
    user=request.headers.get('X-Engineering-Owner','')
    try: uuid.UUID(user)
    except ValueError: return JSONResponse({'detail':'unauthorized'},status_code=401)
    if len(secret)<32 or not hmac.compare_digest(request.headers.get('Authorization','').encode(),('Bearer '+secret).encode()):
        return JSONResponse({'detail':'unauthorized'},status_code=401)
    raw=await request.body()
    if len(raw)>10000: return JSONResponse({'detail':'request too large'},status_code=413)
    body=json.loads(raw)
    if body.get('operation') not in OPERATIONS: return JSONResponse({'detail':'unknown operation'},status_code=400)
    missing=[name for name in REQUIRED_STORAGE if not os.getenv(name)]
    if missing:
        return JSONResponse({'detail':'engineering 缺少私有文件存储配置：'+', '.join(missing),
                             'code':'NATIVE_STORAGE_CONFIG_MISSING'},status_code=503)
    token=owner_id.set(user)
    with tempfile.TemporaryDirectory(prefix='native-') as directory:
        work=workspace.set(directory)
        try:
            return await supervised(body)
        except Exception as exc:
            import logging
            logging.exception('Native operation failed: %s',body['operation'])
            if isinstance(exc, FileNotFoundError):
                code,detail='NATIVE_INPUT_NOT_FOUND','工程输入文件不存在或已过期'
            elif isinstance(exc, KeyError) and exc.args and str(exc.args[0]).startswith('SUPABASE_'):
                code,detail='NATIVE_STORAGE_CONFIG_MISSING','engineering 缺少 Supabase 存储环境变量'
            elif '私有文件存储失败' in str(exc):
                code,detail='NATIVE_STORAGE_ACCESS_FAILED','engineering 无法读写 Supabase 私有文件，请检查 URL、Secret Key 和 Storage Bucket'
            else:
                code,detail='NATIVE_OPERATION_FAILED','工程文件子进程失败，请查看 engineering Runtime Logs'
            return JSONResponse({'detail':detail,'code':code,'reference':uuid.uuid4().hex[:12]},status_code=422)
        finally: workspace.reset(work); owner_id.reset(token)

if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='0.0.0.0',port=int(os.getenv('PORT','80')))
