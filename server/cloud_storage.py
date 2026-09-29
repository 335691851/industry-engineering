"""Private Supabase objects with portable references and tenant-scoped local cache."""
import hashlib
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import httpx

from .cloud_context import owner, workspace

PREFIX = 'object://'


def root():
    if workspace.get() is None:
        raise RuntimeError('缺少云端工作区')
    result = Path(workspace.get()) / 'files'
    result.mkdir(parents=True, exist_ok=True)
    return result


def object_key(path):
    relative = Path(path).resolve().relative_to(root().resolve())
    return f'{owner()}/{relative.as_posix()}'


def request(method, key, content=None):
    if not key.startswith(owner() + '/') or '..' in PurePosixPath(key).parts:
        raise PermissionError('不能访问其他用户的工程文件')
    bucket = os.environ.get('SUPABASE_STORAGE_BUCKET', 'engineering-private')
    url = os.environ['SUPABASE_URL'].rstrip('/') + '/storage/v1/object/' + quote(bucket, safe='') + '/' + quote(key, safe='/')
    secret = os.environ['SUPABASE_SECRET_KEY']
    headers = {'apikey': secret}
    if secret.startswith('eyJ'):
        headers['Authorization'] = f'Bearer {secret}'
    if content is not None:
        headers.update({'x-upsert': 'true', 'Content-Type': mimetypes.guess_type(key)[0] or 'application/octet-stream'})
    response = httpx.request(method, url, headers=headers, content=content, timeout=90)
    if response.status_code == 404:
        raise FileNotFoundError('工程对象不存在')
    if not response.is_success:
        raise RuntimeError(f'私有文件存储失败（HTTP {response.status_code}）')
    return response.content


def _headers():
    secret = os.environ['SUPABASE_SECRET_KEY']
    result = {'apikey': secret, 'Content-Type': 'application/json'}
    if secret.startswith('eyJ'):
        result['Authorization'] = f'Bearer {secret}'
    return result


def clear_owner_objects():
    """Delete every Storage object below the authenticated owner's prefix."""
    bucket = os.environ.get('SUPABASE_STORAGE_BUCKET', 'engineering-private')
    base = os.environ['SUPABASE_URL'].rstrip('/') + '/storage/v1/object'
    folders = [owner()]
    files = []
    while folders:
        folder = folders.pop()
        offset = 0
        while True:
            response = httpx.post(
                base + '/list/' + quote(bucket, safe=''), headers=_headers(),
                json={'prefix': folder, 'limit': 1000, 'offset': offset,
                      'sortBy': {'column': 'name', 'order': 'asc'}}, timeout=30)
            if not response.is_success:
                raise RuntimeError(f'私有文件清单读取失败（HTTP {response.status_code}）')
            entries = response.json()
            for item in entries:
                name = str(item.get('name', '')).strip('/')
                if not name: continue
                path = folder + '/' + name
                if item.get('id') or item.get('metadata') is not None:
                    files.append(path)
                else:
                    folders.append(path)
            if len(entries) < 1000: break
            offset += len(entries)
    for start in range(0, len(files), 1000):
        response = httpx.request('DELETE', base + '/' + quote(bucket, safe=''), headers=_headers(),
                                 json={'prefixes': files[start:start + 1000]}, timeout=90)
        if not response.is_success:
            raise RuntimeError(f'私有文件删除失败（HTTP {response.status_code}）')
    return len(files)


def publish(path):
    path = Path(path)
    key = object_key(path)
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    marker = path.with_name(path.name + '.cloud-sha256')
    if not marker.exists() or marker.read_text() != digest:
        request('POST', key, data)
        marker.write_text(digest)
    return PREFIX + key


def materialize(reference):
    key = reference[len(PREFIX):]
    prefix = owner() + '/'
    if not key.startswith(prefix):
        raise PermissionError('工程文件不属于当前用户')
    relative = PurePosixPath(key[len(prefix):])
    if relative.is_absolute() or '..' in relative.parts or '\\' in str(relative) or ':' in str(relative):
        raise PermissionError('工程文件路径无效')
    path = root().joinpath(*relative.parts)
    if not path.is_file():
        data = request('GET', key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.with_name(path.name + '.cloud-sha256').write_text(hashlib.sha256(data).hexdigest())
    return str(path)


def transform(value, downloading=False):
    if isinstance(value, dict): return {k: transform(v, downloading) for k, v in value.items()}
    if isinstance(value, list): return [transform(v, downloading) for v in value]
    if not isinstance(value, str): return value
    if value.startswith(PREFIX):
        if downloading: return materialize(value)
        if not value.startswith(PREFIX + owner() + '/'):
            raise PermissionError('工程文件不属于当前用户')
        return value
    if value.startswith(('{', '[')):
        try: parsed = json.loads(value)
        except (ValueError, RecursionError): return value
        return json.dumps(transform(parsed, downloading), ensure_ascii=False)
    if not downloading and value.startswith(str(root())):
        return publish(value)
    return value


def restore_folder(relative, names):
    for name in names:
        try: materialize(PREFIX + owner() + '/' + (PurePosixPath(relative) / name).as_posix())
        except FileNotFoundError: pass


def publish_folder(folder):
    for path in Path(folder).rglob('*'):
        if path.is_file() and not path.name.endswith('.cloud-sha256'):
            publish(path)
