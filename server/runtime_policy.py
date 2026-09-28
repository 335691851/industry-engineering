"""Prevent the local, stateful application from silently running on ephemeral hosts."""
import os


def require_local_persistence():
    if os.getenv('VERCEL') or os.getenv('ENGINEERING_RUNTIME', 'local') != 'local':
        raise RuntimeError(
            '当前业务后端依赖 SQLite、本地文件和后台线程，不能直接部署到无服务器运行时。'
            '请使用 deploy/ 中的独立云端入口；完整业务数据迁移尚未完成。'
        )
