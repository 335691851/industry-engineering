"""Produce a secret-free GitHub repository with three independent Vercel roots."""
import hashlib
import json
import shutil
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
IGNORE=shutil.ignore_patterns('node_modules','.next','.swc','.well-known','.workflow-data','__pycache__','*.pyc','.env','.env.local','.env.production')

def package():
    if not (ROOT/'dist/index.html').is_file(): raise ValueError('先运行 npm ci 和 npm run build')
    target=ROOT/'.cloud-build'/('hobby-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S'))
    target.mkdir(parents=True)
    shutil.copytree(ROOT/'apps',target/'apps',ignore=IGNORE)
    shutil.copytree(ROOT/'dist',target/'apps/platform/public',dirs_exist_ok=True)
    # Include editable frontend sources as well as the deployment-ready static build.
    frontend=target/'frontend-source'
    frontend.mkdir()
    for file in ('package.json','package-lock.json','tsconfig.json','vite.config.ts','index.html','cad-studio.html'):
        shutil.copyfile(ROOT/file,frontend/file)
    for directory in ('src','public'):
        shutil.copytree(ROOT/directory,frontend/directory,ignore=IGNORE)
    (frontend/'scripts').mkdir()
    shutil.copyfile(ROOT/'scripts/prepare-cad-studio.mjs',frontend/'scripts/prepare-cad-studio.mjs')
    for name in ('agent-api','engineering'):
        app=target/'apps'/name
        shutil.copytree(ROOT/'server',app/'server',ignore=IGNORE)
        if name=='agent-api':
            for file in ('requirements.txt','requirements-cloud.txt'): shutil.copyfile(ROOT/file,app/file)
        else:
            (app/'scripts').mkdir(exist_ok=True)
            shutil.copyfile(ROOT/'scripts/cloud_native_smoke.py',app/'scripts/cloud_native_smoke.py')
    (target/'supabase/migrations').mkdir(parents=True)
    for migration in ('20260928005941_engineering_cloud_business.sql',
                      '20260928020000_vercel_task_dispatch.sql','20260928040000_hobby_activities.sql'):
        shutil.copyfile(ROOT/'supabase/migrations'/migration,target/'supabase/migrations'/migration)
    (target/'scripts').mkdir()
    for file in ('setup_cloud_checkpoints.py','check_hobby_bundle.py'):
        shutil.copyfile(ROOT/'scripts'/file,target/'scripts'/file)
    shutil.copyfile(ROOT/'docs/Hobby三项目部署.md',target/'README.md')
    shutil.copyfile(ROOT/'docs/开源DWG替代方案.md',target/'DWG说明.md')
    shutil.copyfile(ROOT/'Agent说明.md',target/'Agent说明.md')
    (target/'.github/workflows').mkdir(parents=True)
    shutil.copyfile(ROOT/'deploy/hobby-verify.yml',target/'.github/workflows/verify.yml')
    (target/'.gitignore').write_text('.env\n.env.*\n!.env.example\nnode_modules/\n.next/\n.vercel/\n__pycache__/\n*.pyc\n',encoding='utf-8')
    files=[p for p in target.rglob('*') if p.is_file()]
    manifest={p.relative_to(target).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    (target/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    from check_hobby_bundle import check
    check(target)
    archive=shutil.make_archive(str(target),'zip',target)
    print(archive)
    return target

if __name__=='__main__': package()
