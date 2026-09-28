"""Bound native/Agent execution before the hosting request deadline."""
import asyncio
import json
import os
import signal
import sys
import tempfile
from pathlib import Path

from starlette.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from .cloud_context import owner, owner_id, workspace


async def supervised_task(task_id):
    from . import cloud_tasks
    timeout = min(240, max(30, int(os.getenv('ENGINEERING_TASK_TIMEOUT_SECONDS','240'))))
    with tempfile.TemporaryDirectory(prefix='engineering-result-') as folder:
        output = str(Path(folder)/'result.json')
        options = {'start_new_session':True} if os.name != 'nt' else {}
        process = await asyncio.create_subprocess_exec(sys.executable,'-m','server.cloud_runner',
            task_id, owner(), output, **options)
        try:
            await asyncio.wait_for(process.wait(),timeout)
        except (TimeoutError, asyncio.CancelledError):
            if process.returncode is None:
                if os.name != 'nt': os.killpg(process.pid,signal.SIGKILL)
                else: process.kill()
                await process.wait()
            await mark_interrupted(task_id,'任务超过执行时限或被取消，已保留已保存成果，请检查后重试')
            return JSONResponse({'state':'failed'})
        if process.returncode != 0 or not Path(output).is_file():
            await mark_interrupted(task_id,'工程子进程异常退出，已保留已保存成果，请检查云端运行日志')
            return JSONResponse({'state':'failed'})
        result=json.loads(Path(output).read_text(encoding='utf-8'))
        return JSONResponse(result['body'],status_code=result['status'])


async def mark_interrupted(task_id,reason):
    from . import cloud_tasks
    item=await run_in_threadpool(cloud_tasks.task,task_id)
    if item and item['state'] not in ('completed','failed'):
        await run_in_threadpool(cloud_tasks.fail,item,reason)


async def run(task_id,user,output):
    from .cloud_entry import app
    token=owner_id.set(user)
    with tempfile.TemporaryDirectory(prefix='engineering-task-') as folder:
        work=workspace.set(folder)
        try:
            response=await app.execute_task(task_id)
            Path(output).write_text(json.dumps({'status':response.status_code,
                'body':json.loads(response.body)}),encoding='utf-8')
        finally: workspace.reset(work); owner_id.reset(token)


if __name__ == '__main__':
    asyncio.run(run(*sys.argv[1:]))
