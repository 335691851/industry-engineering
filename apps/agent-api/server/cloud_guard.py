"""Shared Postgres admission limits; signed platform provenance, never browser IP claims."""
import hashlib
import hmac
import os
from .cloud_config import database_url
import time

from starlette.responses import JSONResponse


def client_key(request):
    secret = os.getenv('ENGINEERING_SERVICE_TOKEN', '')
    stamp = request.headers.get('x-engineering-time', '')
    ip = request.headers.get('x-engineering-client', '')
    proof = request.headers.get('x-engineering-proof', '')
    message = f'{stamp}\n{request.method}\n{request.url.path}\n{ip}'
    expected = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    if len(secret) < 32 or not stamp.isdigit() or abs(time.time()-int(stamp)) > 60 or not ip or not hmac.compare_digest(proof, expected):
        raise PermissionError('请通过平台入口访问，或检查三个项目的服务密钥是否一致')
    return hmac.new(secret.encode(), ip.encode(), hashlib.sha256).hexdigest()


def limit(kind, key):
    import psycopg
    maximum, seconds = {'read': (120, 60), 'write': (20, 60), 'signup': (5, 3600), 'generation': (30, 3600)}[kind]
    # Administrative connection is used only for anonymous counters, never business rows.
    with psycopg.connect(database_url(), connect_timeout=10) as con:
        con.execute("DELETE FROM engineering.access_buckets WHERE expires_at < now()")
        row = con.execute("""INSERT INTO engineering.access_buckets (key, window_id, hits, expires_at)
            VALUES (%s, floor(extract(epoch FROM now())/%s)::bigint, 1, now()+interval '2 hours')
            ON CONFLICT (key, window_id) DO UPDATE SET hits=access_buckets.hits+1
            WHERE access_buckets.hits < %s RETURNING hits""", (kind+':'+key, seconds, maximum)).fetchone()
    if not row:
        return JSONResponse({'detail': f'操作过于频繁，请在 {seconds} 秒后重试'}, status_code=429, headers={'Retry-After': str(seconds)})
    return None


def acquire_slot(con):
    capacity = max(1, min(8, int(os.getenv('ENGINEERING_MAX_CONCURRENT_TASKS', '2'))))
    for index in range(capacity):
        if con.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS locked',
                       (f'engineering:global-worker:{index}',)).fetchone()['locked']:
            return True
    return False
