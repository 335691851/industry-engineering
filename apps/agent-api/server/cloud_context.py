"""Per-request tenant identity and disposable working files (never persistent disk)."""
import os
from contextvars import ContextVar
from pathlib import Path

owner_id = ContextVar('engineering_owner', default='')
workspace = ContextVar('engineering_workspace', default=None)


def enabled():
    return os.getenv('ENGINEERING_RUNTIME') == 'cloud'


def owner():
    value = owner_id.get()
    if not value:
        raise PermissionError('缺少经过验证的云端用户身份')
    return value


class RequestPath(os.PathLike):
    def __init__(self, suffix=''):
        self.suffix = suffix

    def path(self):
        root = workspace.get()
        if root is None:
            raise RuntimeError('云端文件操作必须位于请求工作区')
        path = Path(root) / self.suffix
        path.mkdir(parents=True, exist_ok=True)
        return path

    def __fspath__(self): return str(self.path())
    def __str__(self): return str(self.path())
    def __truediv__(self, name): return self.path() / name
    def __getattr__(self, name): return getattr(self.path(), name)
