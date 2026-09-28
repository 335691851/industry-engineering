"""Postgres adapter for existing parameterized business queries; RLS on every request."""
import os
from .cloud_config import database_url
import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import urlparse
from contextlib import contextmanager

from .cloud_context import owner

INSERT_COLUMNS = {
    'messages': 'id,project_id,role,mode,content,part_id,created_at',
    'resources': 'id,part_id,name,kind,file_path,created_at',
    'mbom_links': 'id,project_id,parent_id,child_id,quantity,evidence,confidence',
    'engineering_memory': 'id,category,title,content,source,file_path,created_at',
    'jobs': 'id,project_id,status,total,completed,current,errors,created_at,updated_at',
}


def translate(sql):
    if 'INSERT OR REPLACE' in sql.upper():
        raise RuntimeError('云端禁止运行本地样例覆盖导入')
    # Legacy queries use double quotes only for string literals, not identifiers.
    chunks = re.split(r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\")", sql)
    for i, chunk in enumerate(chunks):
        if chunk.startswith('"'):
            chunks[i] = "'" + chunk[1:-1].replace('""', '"').replace("'", "''") + "'"
        elif not chunk.startswith("'"):
            chunks[i] = chunk.replace('?', '%s')
    result = ''.join(chunks)
    for table, columns in INSERT_COLUMNS.items():
        result = re.sub(rf'INSERT INTO {table}\s+VALUES', f'INSERT INTO {table} ({columns}) VALUES', result, flags=re.I)
    result = result.replace('WHEN %s THEN', 'WHEN (%s)::boolean THEN')
    return result


def raw_connection(schema='engineering', autocommit=False):
    import psycopg
    from psycopg.rows import dict_row
    con = psycopg.connect(database_url(), row_factory=dict_row, autocommit=True,
                          prepare_threshold=None, connect_timeout=15)
    con.execute('SET ROLE engineering_app')
    con.execute("SELECT set_config('search_path', %s, false)", (schema + ',public',))
    con.execute("SELECT set_config('request.jwt.claim.sub', %s, false)", (owner(),))
    con.autocommit = autocommit
    return con


class Cursor:
    def __init__(self, cursor): self.cursor = cursor
    @property
    def rowcount(self): return self.cursor.rowcount
    def portable(self, value):
        if isinstance(value, uuid.UUID): return str(value)
        if isinstance(value, (date, datetime)): return value.isoformat()
        if isinstance(value, Decimal): return float(value)
        if isinstance(value, dict):
            # These columns implement tenant isolation and Postgres ordering.
            # They are not part of the SQLite-compatible business data contract.
            return {key: self.portable(item) for key, item in value.items()
                    if key not in ('owner_id', 'rowid')}
        if isinstance(value, (list, tuple)): return [self.portable(item) for item in value]
        return value
    def convert(self, value):
        if value is None: return None
        from .cloud_storage import transform
        return transform(self.portable(dict(value)), downloading=True)
    def fetchone(self): return self.convert(self.cursor.fetchone())
    def fetchall(self): return [self.convert(v) for v in self.cursor.fetchall()]
    def __iter__(self): return iter(self.fetchall())


class Connection:
    def __init__(self, raw): self.raw = raw
    def execute(self, sql, params=()):
        from .cloud_storage import transform
        return Cursor(self.raw.execute(translate(sql), tuple(transform(p) for p in params)))


@contextmanager
def connect():
    with raw_connection() as con:
        yield Connection(con)
