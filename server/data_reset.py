"""Tenant-scoped removal of engineering business data and generated files."""
import shutil

from . import cloud_storage, db
from .cloud_context import enabled


def clear_business_data():
    """Permanently remove the current workspace's business records and files."""
    with db.connect() as con:
        counts = {
            'projects': con.execute('SELECT count(*) AS count FROM projects').fetchone()['count'],
            'parts': con.execute('SELECT count(*) AS count FROM parts').fetchone()['count'],
            'tasks': con.execute('SELECT count(*) AS count FROM cloud_tasks').fetchone()['count'] if enabled() else
                     con.execute('SELECT count(*) AS count FROM jobs').fetchone()['count'],
            'memory': con.execute('SELECT count(*) AS count FROM engineering_memory').fetchone()['count'],
        }
        if enabled():
            # LangGraph thread IDs begin with the authenticated owner ID. RLS remains
            # active on these tables, so this cannot remove another tenant's state.
            from .cloud_context import owner
            thread_prefix = owner() + ':%'
            con.execute('DELETE FROM engineering_agent.checkpoint_writes WHERE thread_id LIKE ?', (thread_prefix,))
            con.execute('DELETE FROM engineering_agent.checkpoint_blobs WHERE thread_id LIKE ?', (thread_prefix,))
            con.execute('DELETE FROM engineering_agent.checkpoints WHERE thread_id LIKE ?', (thread_prefix,))
            files = cloud_storage.clear_owner_objects()
        else:
            files = sum(1 for path in db.FILES.rglob('*') if path.is_file()) if db.FILES.exists() else 0
        # Project cascades remove parts, MBOM links, messages, resources, jobs,
        # cloud tasks and cloud task activities. Memory is intentionally independent.
        con.execute('DELETE FROM projects')
        con.execute('DELETE FROM engineering_memory')
    if not enabled():
        shutil.rmtree(db.FILES, ignore_errors=True)
        db.FILES.mkdir(parents=True, exist_ok=True)
    return {**counts, 'files': files, 'cleared': True}
