from contextlib import contextmanager
from .cloud_context import enabled, owner


def thread_id(project_id):
    return f'{owner()}:{project_id}' if enabled() else project_id


@contextmanager
def saver():
    if enabled():
        from langgraph.checkpoint.postgres import PostgresSaver
        from .cloud_db import raw_connection
        with raw_connection('engineering_agent', autocommit=True) as connection:
            yield PostgresSaver(connection)
    else:
        from langgraph.checkpoint.sqlite import SqliteSaver
        from .db import DATA
        DATA.mkdir(exist_ok=True)
        with SqliteSaver.from_conn_string(str(DATA / 'agent_checkpoints.db')) as checkpoint:
            yield checkpoint
