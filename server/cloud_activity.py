"""Persist completed expensive activities per task; yield before the next budgeted call.

Only successful outputs are memoized. A killed, uncommitted operation is retried,
not treated as success. Task locks serialize replays. No task runs across tenants.
"""
import functools
import hashlib
import json
import time
from contextvars import ContextVar
from pathlib import Path

task_context=ContextVar('engineering_activity',default=None)

class TaskYield(BaseException):
    """Control signal that must bypass business `except Exception` handlers."""


def begin(task_id):
    return task_context.set({'id':task_id,'deadline':time.monotonic()+210})


def activity_call(name,fn,*args,**kwargs):
    context=task_context.get()
    if not context: return fn(*args,**kwargs)
    from .cloud_storage import transform, publish, materialize
    from .db import connect,row,now
    def portable(v):
        if isinstance(v,Path): return publish(v) if v.is_file() else str(v)
        if isinstance(v,dict): return {k:portable(x) for k,x in v.items()}
        if isinstance(v,(tuple,list)): return [portable(x) for x in v]
        return transform(v)
    key=hashlib.sha256(json.dumps([name,portable(args),portable(kwargs)],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    with connect() as con:
        cached=row(con,'SELECT result FROM cloud_activities WHERE task_id=? AND activity_key=?',(context['id'],key))
    if cached: return json.loads(cached['result'])
    if name!='message' and time.monotonic()+125>context['deadline']: raise TaskYield()
    result=fn(*args,**kwargs)
    encoded=json.dumps(portable(result),ensure_ascii=False)
    with connect() as con:
        con.execute('INSERT INTO cloud_activities(task_id,activity_key,result,created_at) VALUES (?,?,?,?) ON CONFLICT (task_id,activity_key) DO NOTHING',
                    (context['id'],key,encoded,now()))
    return result


def activity(name):
    def decorate(fn):
        @functools.wraps(fn)
        def wrapped(*args,**kwargs): return activity_call(name,fn,*args,**kwargs)
        return wrapped
    return decorate


def commit_activity(name):
    """A successful commit is reused when an enclosing Agent resumes."""
    def decorate(fn):
        @functools.wraps(fn)
        def wrapped(project,part,instruction):
            return activity_call(name,lambda *_:fn(project,part,instruction),
                                 project['id'],part['id'] if part else None,instruction)
        return wrapped
    return decorate
