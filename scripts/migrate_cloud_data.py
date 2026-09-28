"""Explicit SQLite -> private Postgres migration. Dry-run by default; no source deletion."""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
TABLES=('projects','parts','mbom_links','messages','resources','engineering_memory','jobs')


def migrate(database, owner, apply=False):
    owner=str(uuid.UUID(owner))
    source=sqlite3.connect(f'{Path(database).resolve().as_uri()}?mode=ro',uri=True)
    source.row_factory=sqlite3.Row
    contents={table:[dict(row) for row in source.execute(f'SELECT * FROM {table}')] for table in TABLES}
    source.close()
    counts={table:len(rows) for table,rows in contents.items()}
    if not apply:return {'dry_run':True,'counts':counts}
    os.environ['ENGINEERING_RUNTIME']='cloud'
    from server.cloud_context import owner_id,workspace
    from server.cloud_db import connect
    from server.cloud_storage import root
    import hashlib
    who=owner_id.set(owner)
    with tempfile.TemporaryDirectory(prefix='engineering-migrate-') as temp:
        work=workspace.set(temp)
        try:
            def convert(value):
                if isinstance(value,dict):return {k:convert(v) for k,v in value.items()}
                if isinstance(value,list):return [convert(v) for v in value]
                if not isinstance(value,str):return value
                if value.startswith(('{','[')):
                    try:return json.dumps(convert(json.loads(value)),ensure_ascii=False)
                    except json.JSONDecodeError:return value
                path=Path(value)
                if not path.is_absolute():return value
                resolved=path.resolve()
                allowed=[Path(database).resolve().parent/'files',ROOT/'sample']
                base=next((base for base in allowed if resolved.is_relative_to(base.resolve())),None)
                if base is None:
                    # Ordinary text may contain paths; only recognized file fields should carry actual files.
                    if path.suffix.lower() in ('.pdf','.dxf','.dwg','.xlsx','.svg','.json'):
                        raise ValueError('迁移发现工作区外的文件路径；请先复制到 data/files')
                    return value
                if not resolved.is_file():raise ValueError('迁移源文件缺失：'+resolved.name)
                relative=resolved.relative_to(base.resolve())
                target=root()/relative if base.name=='files' else root()/'memory-import'/(hashlib.sha256(resolved.read_bytes()).hexdigest()+resolved.suffix)
                target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(resolved,target)
                return str(target)
            with connect() as con:
                for table,records in contents.items():
                    for record in records:
                        record=convert(record)
                        if table=='jobs' and record['status'] in ('等待中','运行中'):
                            record['status']='中断'
                        keys=list(record)
                        # Collision aborts the transaction; never silently overwrite an existing cloud project.
                        con.execute(f'INSERT INTO {table} ({",".join(keys)}) VALUES ({",".join("?" for _ in keys)})',tuple(record[k] for k in keys))
        finally:workspace.reset(work);owner_id.reset(who)
    return {'dry_run':False,'counts':counts,'checkpoint_note':'历史会话消息已迁移；旧执行 checkpoint 不自动恢复，避免重放未完成的工具写入。'}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--sqlite',default=str(ROOT/'data/engineering.db'))
    parser.add_argument('--owner',required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    print(json.dumps(migrate(args.sqlite,args.owner,args.apply),ensure_ascii=False,indent=2))
