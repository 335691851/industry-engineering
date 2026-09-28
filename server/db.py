import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from .cloud_context import enabled, RequestPath

ROOT = Path(__file__).resolve().parents[1]
DATA = RequestPath() if enabled() else ROOT / 'data'
FILES = RequestPath('files') if enabled() else DATA / 'files'
DB = None if enabled() else DATA / 'engineering.db'


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex[:12]


def connect():
    if enabled():
        from .cloud_db import connect as cloud_connect
        return cloud_connect()
    from .runtime_policy import require_local_persistence
    require_local_persistence()
    DATA.mkdir(exist_ok=True)
    FILES.mkdir(exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    return con


def init():
    if enabled():
        return  # Apply versioned migrations explicitly; never reset active jobs on cold starts.
    with connect() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS projects (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, drawing_no TEXT DEFAULT '',
          source_path TEXT NOT NULL, source_text TEXT DEFAULT '',
          analysis TEXT DEFAULT '{}', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS parts (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          parent_id TEXT REFERENCES parts(id), name TEXT NOT NULL, drawing_no TEXT DEFAULT '',
          kind TEXT DEFAULT '零件', material TEXT DEFAULT '', status TEXT DEFAULT '待解析',
          specifications TEXT DEFAULT '{}', geometry TEXT DEFAULT '{}', process TEXT DEFAULT '{}',
          source_pdf TEXT DEFAULT '', drawing_pdf TEXT DEFAULT '', drawing_dxf TEXT DEFAULT '',
          drawing_dwg TEXT DEFAULT '', updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          role TEXT NOT NULL, mode TEXT NOT NULL, content TEXT NOT NULL,
          part_id TEXT DEFAULT '', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS resources (
          id TEXT PRIMARY KEY, part_id TEXT NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
          name TEXT NOT NULL, kind TEXT NOT NULL, file_path TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS mbom_links (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          parent_id TEXT REFERENCES parts(id) ON DELETE CASCADE,
          child_id TEXT NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
          quantity REAL NOT NULL DEFAULT 1, evidence TEXT DEFAULT '', confidence TEXT DEFAULT '待复核'
        );
        CREATE TABLE IF NOT EXISTS engineering_memory (
          id TEXT PRIMARY KEY, category TEXT NOT NULL, title TEXT NOT NULL,
          content TEXT NOT NULL, source TEXT NOT NULL, file_path TEXT DEFAULT '',
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          status TEXT NOT NULL, total INTEGER NOT NULL, completed INTEGER DEFAULT 0,
          current TEXT DEFAULT '', errors TEXT DEFAULT '[]', created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        ''')
        for table, column, definition in (
            ('projects', 'stage', "TEXT DEFAULT '待解析'"),
            ('parts', 'quantity', 'REAL DEFAULT 1'),
        ):
            if column not in {r['name'] for r in con.execute(f'PRAGMA table_info({table})')}:
                con.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
        con.execute("""UPDATE projects SET stage='MBOM待确认'
                       WHERE stage='待解析' AND EXISTS
                       (SELECT 1 FROM parts WHERE parts.project_id=projects.id)""")
        con.execute("UPDATE jobs SET status='中断',updated_at=? WHERE status IN ('等待中','运行中')", (now(),))
        # Existing projects had one parent per part. Preserve that relation as one usage link.
        con.execute('''INSERT INTO mbom_links (id,project_id,parent_id,child_id,quantity,evidence,confidence)
                       SELECT lower(hex(randomblob(12))),p.project_id,p.parent_id,p.id,p.quantity,'旧版 MBOM 迁移','待复核'
                       FROM parts p WHERE NOT EXISTS (SELECT 1 FROM mbom_links l WHERE l.child_id=p.id)''')


def rows(con, sql, params=()):
    return [dict(row) for row in con.execute(sql, params).fetchall()]


def row(con, sql, params=()):
    result = con.execute(sql, params).fetchone()
    return dict(result) if result else None


def unpack(record):
    if not record:
        return record
    for key in ('analysis', 'specifications', 'geometry', 'process'):
        if key in record:
            try:
                record[key] = json.loads(record[key] or '{}')
            except json.JSONDecodeError:
                record[key] = {}
    return record


def project_bundle(project_id):
    with connect() as con:
        project = row(con, 'SELECT * FROM projects WHERE id=?', (project_id,))
        if not project:
            return None
        project = unpack(project)
        project['parts'] = [unpack(p) for p in rows(con, 'SELECT * FROM parts WHERE project_id=? ORDER BY rowid', (project_id,))]
        project['mbom_links'] = rows(con, 'SELECT * FROM mbom_links WHERE project_id=? ORDER BY rowid', (project_id,))
        names = {p['id']: p['name'] for p in project['parts']}
        for part in project['parts']:
            usages = [link for link in project['mbom_links'] if link['child_id'] == part['id']]
            part['usages'] = [{'link_id': link['id'], 'parent_id': link['parent_id'],
                               'parent_name': names.get(link['parent_id'], project['name']),
                               'quantity': link['quantity']} for link in usages]
            part['resources'] = rows(con, 'SELECT id,name,kind,created_at FROM resources WHERE part_id=? ORDER BY created_at DESC', (part['id'],))
        from .workflow import workflow_state
        for part in project['parts']:
            part['workflow'] = workflow_state(project, part)
        project['messages'] = rows(con, 'SELECT * FROM messages WHERE project_id=? ORDER BY created_at', (project_id,))
        project['job'] = row(con, 'SELECT * FROM jobs WHERE project_id=? ORDER BY created_at DESC LIMIT 1', (project_id,))
        if project['job']:
            project['job']['errors'] = json.loads(project['job']['errors'] or '[]')
        return project


from .cloud_activity import activity


@activity('message')
def add_message(project_id, role, mode, content, part_id=''):
    with connect() as con:
        con.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?)',
                    (uid(), project_id, role, mode, content, part_id, now()))
