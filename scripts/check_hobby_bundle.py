"""Static release gate; never prints secret values."""
import json
import re
import sys
from pathlib import Path

def check(root):
    root=Path(root)
    for name in ('platform','agent-api','engineering'):
        folder=root/'apps'/name
        config=json.loads((folder/'vercel.json').read_text())
        assert not any('dockerfile' in key.lower() or 'containerfile' in key.lower()
                       for key in config.get('functions',{})), 'Dockerfile is not a Serverless function pattern'
        for fn in config.get('functions',{}).values():
            assert fn.get('maxDuration',300)<=300, name+' duration exceeds Hobby'
            assert fn.get('memory',2048)<=2048, name+' memory exceeds Hobby'
        for cron in config.get('crons',[]):
            assert re.fullmatch(r'\d+ \d+ \* \* \*',cron['schedule']), 'Cron must be daily'
    assert (root/'apps/platform/public/index.html').is_file()
    for path in root.rglob('*'):
        if not path.is_file(): continue
        assert path.name not in ('.env','.env.local','.env.production'), 'Private env file included'
        assert path.suffix.lower() not in ('.db','.sqlite','.sqlite3'), 'Business database included'
        if path.suffix in ('.py','.js','.mjs','.json','.md','.ts') and path.stat().st_size<2000000:
            assert not re.search(rb'sk-[A-Za-z0-9]{24,}',path.read_bytes()), 'Possible API secret in '+str(path.relative_to(root))
    print('PASS: three project roots, Hobby limits, frontend, no local env/database/API key')

if __name__=='__main__': check(sys.argv[1] if len(sys.argv)>1 else Path(__file__).resolve().parents[1])
