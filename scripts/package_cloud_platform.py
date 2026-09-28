"""Prepare isolated Vercel artifacts. Never copy data/, .env, logs, or sample drawings."""
import argparse
import json
import shutil
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]


def package(backend):
    address = urlparse(backend)
    if address.scheme != 'https' or not address.netloc or address.username or address.password or address.query or address.path not in ('', '/'):
        raise ValueError('--backend 必须是可信后端的 HTTPS 域名，不含路径或凭证')
    if not (ROOT / 'dist/index.html').is_file():
        raise ValueError('请先运行 npm run build')
    # Fresh version folder avoids including stale build artifacts from a previous package.
    from datetime import datetime, timezone
    target = ROOT / '.cloud-build' / ('platform-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S'))
    front = target / 'frontend'
    shutil.copytree(ROOT / 'dist', front)
    (front / 'vercel.json').write_text(json.dumps({
        'framework': None,
        'rewrites': [
            {'source': '/api/:path*', 'destination': backend.rstrip('/') + '/api/:path*'},
            {'source': '/((?!assets/|cad-assets/|cad-studio.html).*)', 'destination': '/index.html'},
        ],
        'headers': [{'source':'/api/:path*','headers':[{'key':'Cache-Control','value':'private, no-store'}]}],
    }, indent=2), encoding='utf-8')
    python_target = target / 'vercel-python'
    shutil.copytree(ROOT / 'server', python_target / 'server', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ('requirements.txt', 'requirements-cloud.txt'):
        shutil.copyfile(ROOT / name, python_target / name)
    (python_target / 'requirements.txt').write_text((ROOT/'requirements.txt').read_text() + '\n' +
        '\n'.join(line for line in (ROOT/'requirements-cloud.txt').read_text().splitlines() if not line.startswith('-r')), encoding='utf-8')
    (python_target / 'app.py').write_text('from server.cloud_entry import app\n', encoding='utf-8')
    (python_target / 'vercel.json').write_text(json.dumps({'functions':{'app.py':{'maxDuration':300}}},indent=2),encoding='utf-8')
    (python_target / 'pyproject.toml').write_text('[project]\nname="engineering-cloud-api"\nversion="0.1.0"\nrequires-python=">=3.12,<3.13"\n[tool.vercel]\nentrypoint="app:app"\n',encoding='utf-8')
    return target


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--backend',required=True)
    args=parser.parse_args()
    print(package(args.backend))
