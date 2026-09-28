"""Create an isolated Vercel project without local data, secrets or CAD binaries."""
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def package(destination=None):
    target = Path(destination) if destination else ROOT / '.cloud-build' / 'vercel-compute'
    target.mkdir(parents=True, exist_ok=True)
    sources = {
        'api/index.py': ROOT / 'deploy/vercel-compute/api/index.py',
        'vercel.json': ROOT / 'deploy/vercel-compute/vercel.json',
        'requirements.txt': ROOT / 'deploy/vercel-compute/requirements.txt',
        'engineering_core/engineering_model.py': ROOT / 'server/engineering_model.py',
        'engineering_core/manufacturing.py': ROOT / 'server/manufacturing.py',
    }
    for name, source in sources.items():
        dest = target / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    (target / 'engineering_core/__init__.py').write_text('', encoding='utf-8')
    manifest = {name: hashlib.sha256(source.read_bytes()).hexdigest() for name, source in sources.items()}
    (target / 'build-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return target


if __name__ == '__main__':
    print(package())
