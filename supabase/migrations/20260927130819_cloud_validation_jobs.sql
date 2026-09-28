-- Independent cloud validation slice; does not claim to migrate the local business DB.
create table public.cloud_validation_jobs (
    id uuid primary key,
    owner_id uuid not null references auth.users(id) on delete cascade,
    input jsonb not null check (jsonb_typeof(input) = 'object'),
    input_hash text not null check (input_hash ~ '^[0-9a-f]{64}$'),
    status text not null default 'queued' check (status in ('queued', 'running', 'completed', 'failed')),
    report jsonb,
    error text not null default '',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index cloud_validation_jobs_owner_created on public.cloud_validation_jobs(owner_id, created_at desc);
alter table public.cloud_validation_jobs enable row level security;
revoke all on public.cloud_validation_jobs from anon, authenticated;
grant select on public.cloud_validation_jobs to authenticated;
grant select, insert, update, delete on public.cloud_validation_jobs to service_role;
create policy "Owners read their validation jobs"
    on public.cloud_validation_jobs for select to authenticated
    using ((select auth.uid()) = owner_id);
-- Clients cannot forge reports or change job status. Writes pass through the Worker,
-- which validates Auth and scopes every privileged lookup/update to the owner.
