"""Safe, actionable cloud failure messages without provider credentials or SQL values."""
import logging
import traceback
import uuid


def describe_failure(error):
    import psycopg
    import httpx
    reference = uuid.uuid4().hex[:12]
    code = 'CLOUD_SERVICE_ERROR'
    detail = '云端服务执行失败，请凭诊断编号查看 agent-api Runtime Logs'
    if isinstance(error, KeyError) and error.args and error.args[0] in (
        'SUPABASE_URL', 'SUPABASE_PUBLISHABLE_KEY', 'SUPABASE_DB_URL', 'SUPABASE_SECRET_KEY'):
        code = 'CONFIG_MISSING'
        detail = f'agent-api 缺少环境变量 {error.args[0]}，请配置后重新部署'
    elif isinstance(error, psycopg.Error):
        state = error.sqlstate or ''
        # Inspect provider text locally, but never return or log it: it can contain secrets.
        text = str(error).lower()
        if state.startswith('28') or 'password authentication failed' in text:
            code = 'DATABASE_AUTH_FAILED'
            detail = '数据库认证失败，请核对 agent-api 的 SUPABASE_DB_URL 用户名和数据库密码（不是 Supabase API Key），并对密码特殊字符做 URL 编码'
        elif state == '42P01':
            code = 'DATABASE_SCHEMA_MISSING'
            detail = '数据库缺少所需表，请确认连接到已初始化的 Supabase 项目并应用迁移'
        elif state == '42501':
            code = 'DATABASE_PERMISSION_DENIED'
            detail = '数据库权限不足，请检查连接用户、engineering_app 授权及限流表迁移'
        elif isinstance(error, psycopg.OperationalError):
            code = 'DATABASE_CONNECTION_FAILED'
            detail = '无法连接 Supabase 数据库。请从 Connect 复制 Session pooler 连接串（端口 5432，用户名通常为 postgres.项目ID）到 agent-api 的 SUPABASE_DB_URL；同时核对密码、网络限制。不要使用 Transaction pooler 6543'
        else:
            code = 'DATABASE_QUERY_FAILED'
            detail = '数据库操作失败，请凭诊断编号检查 agent-api Runtime Logs 中的 SQLSTATE'
    elif isinstance(error, httpx.HTTPError):
        code = 'AUTH_PROVIDER_UNAVAILABLE'
        detail = '无法访问外部服务，请检查 Supabase 地址及网络连接；已有工作区不会删除'
    frames = [{'file': frame.filename.replace('\\', '/').rsplit('/', 1)[-1],
               'line': frame.lineno, 'function': frame.name}
              for frame in traceback.extract_tb(error.__traceback__)]
    logging.getLogger('engineering.cloud').error('cloud_failure ref=%s code=%s type=%s sqlstate=%s frames=%s',
        reference, code, type(error).__name__, getattr(error, 'sqlstate', None), frames)
    return {'detail': f'{detail}（诊断编号：{reference}）', 'code': code, 'reference': reference}
