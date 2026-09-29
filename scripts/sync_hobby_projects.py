"""Synchronize independent Vercel roots in this repository; no archive is created."""
import argparse
import ast
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def python_closure(*entrypoints):
    """Return local modules reachable from a service's explicit entrypoints.

    The two containers previously received every server module.  Besides a
    little image waste, that made an Agent-only edit redeploy the large native
    Engineering container.  Static relative imports plus explicit dynamic
    native-operation roots keep each Vercel Root Directory independently
    deployable.  Missing optional modules are ignored here and fail at their
    normal import/build gate instead.
    """
    source = ROOT/'server'
    pending = list(entrypoints)
    result = {'__init__'}
    while pending:
        name = pending.pop()
        path = source/f'{name}.py'
        if name in result or not path.is_file():
            continue
        result.add(name)
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.level:
                continue
            names = [node.module.split('.')[0]] if node.module else [item.name.split('.')[0] for item in node.names]
            pending.extend(item for item in names if (source/f'{item}.py').is_file())
    return [source/f'{name}.py' for name in sorted(result) if (source/f'{name}.py').is_file()]

def synchronize(check=False):
    if not (ROOT/'dist/index.html').is_file(): raise ValueError('先运行 npm ci 和 npm run build')
    mapping={}
    services = {
        'agent-api': python_closure('cloud_entry', 'vercel_start'),
        # native_entry dispatches these operation modules by string.
        'engineering': python_closure('native_entry', 'native_job', 'drawing', 'evidence',
                                      'cad_import', 'cad_artifacts', 'dwg_converter'),
    }
    for name,sources in services.items():
        for source in sources:
            mapping[ROOT/'apps'/name/'server'/source.name]=source
    for name in ('requirements.txt','requirements-cloud.txt'):
        mapping[ROOT/'apps/agent-api'/name]=ROOT/name
    mapping[ROOT/'apps/engineering/scripts/cloud_native_smoke.py']=ROOT/'scripts/cloud_native_smoke.py'
    for source in (ROOT/'dist').rglob('*'):
        if source.is_file(): mapping[ROOT/'apps/platform/public'/source.relative_to(ROOT/'dist')]=source
    manifest=ROOT/'apps/.source-manifest.json'
    previous=json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else {}
    current={destination.relative_to(ROOT).as_posix():hashlib.sha256(source.read_bytes()).hexdigest()
             for destination,source in mapping.items()}
    stale=[]
    for relative,digest in previous.items():
        if relative in current: continue
        path=(ROOT/relative).resolve()
        allowed=[(ROOT/'apps'/n).resolve() for n in ('agent-api/server','engineering/server','platform/public')]
        if not any(path.is_relative_to(folder) for folder in allowed): raise ValueError('同步清单包含非生成路径')
        if path.is_file():
            if hashlib.sha256(path.read_bytes()).hexdigest()!=digest: raise ValueError('旧生成文件已被修改，请先保留修改：'+relative)
            stale.append(path)
    mismatches=[d for d,s in mapping.items() if not d.is_file() or d.read_bytes()!=s.read_bytes()]
    if check:
        if mismatches or stale: raise ValueError('部署目录未同步，请运行 python scripts/sync_hobby_projects.py')
        print('PASS: Vercel project sources match server/ and dist/')
        return
    for destination in mismatches:
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(mapping[destination].read_bytes())
    for path in stale: path.unlink()
    manifest.write_text(json.dumps(current,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Synchronized {len(mapping)} files into three Vercel projects; no archive created.')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true')
    synchronize(parser.parse_args().check)
