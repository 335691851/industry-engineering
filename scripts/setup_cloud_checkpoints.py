"""Run once with migration credentials, never at server startup."""
import os
import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row


def setup():
    with psycopg.connect(os.environ['SUPABASE_DB_URL'], autocommit=True, prepare_threshold=None, row_factory=dict_row) as con:
        con.execute('create schema if not exists engineering_agent')
        con.execute('set search_path=engineering_agent,public')
        PostgresSaver(con).setup()
        con.execute('grant usage on schema engineering_agent to engineering_app')
        con.execute('revoke all on schema engineering_agent from anon, authenticated')
        for table in ('checkpoints', 'checkpoint_blobs', 'checkpoint_writes'):
            identifier = psycopg.sql.Identifier(table)
            con.execute(psycopg.sql.SQL('alter table {} enable row level security').format(identifier))
            con.execute(psycopg.sql.SQL('alter table {} force row level security').format(identifier))
            con.execute(psycopg.sql.SQL('drop policy if exists tenant_checkpoints on {}').format(identifier))
            con.execute(psycopg.sql.SQL("create policy tenant_checkpoints on {} to engineering_app using (split_part(thread_id,':',1)=current_setting('request.jwt.claim.sub',true)) with check (split_part(thread_id,':',1)=current_setting('request.jwt.claim.sub',true))").format(identifier))
            con.execute(psycopg.sql.SQL('grant select,insert,update,delete on {} to engineering_app').format(identifier))


if __name__ == '__main__':
    setup()
    print('Checkpoint schema and tenant policies installed.')
