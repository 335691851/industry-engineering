"""Validate database configuration without leaking credentials in exceptions."""
import os
import re
from urllib.parse import urlsplit


def database_url():
    value = os.getenv('SUPABASE_DB_URL', '').strip()
    # Dashboard placeholders sometimes leave brackets around a DNS hostname.
    # Brackets are reserved for IPv6; preserve those and all encoded credentials.
    value = re.sub(r'(?<=@)\[([a-zA-Z0-9.-]+)\](?=[:/]|$)', r'\1', value)
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ('postgres', 'postgresql') or not parsed.hostname:
            raise ValueError()
        port = parsed.port
    except ValueError:
        raise ValueError('SUPABASE_DB_URL 格式错误：请复制 PostgreSQL 连接串，域名不要加方括号，密码中的特殊字符需 URL 编码') from None
    if port == 6543:
        raise ValueError('请使用 Supabase Direct 或 Session pooler 连接串（通常端口 5432），不能使用 Transaction pooler 6543')
    return value
