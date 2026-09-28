-- Provider-neutral dispatch state; existing owner RLS applies.
alter table engineering.cloud_tasks add column if not exists workflow_run_id text not null default '';
