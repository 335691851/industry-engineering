-- Private schema: no business writes through the public Data API.
create schema if not exists engineering;
do $$ begin
  if not exists (select 1 from pg_roles where rolname='engineering_app') then
    create role engineering_app nologin nobypassrls;
  end if;
end $$;
grant engineering_app to postgres;
grant usage on schema engineering to engineering_app;
revoke all on schema engineering from anon, authenticated;

create table engineering.projects (
 id text primary key, name text not null, drawing_no text default '', source_path text not null,
 source_text text default '', analysis text default '{}', created_at text not null, stage text default '待解析',
 owner_id uuid not null default nullif(current_setting('request.jwt.claim.sub',true),'')::uuid references auth.users(id),
 rowid bigint generated always as identity, unique(id,owner_id)
);
create table engineering.parts (
 id text primary key, project_id text not null, parent_id text, name text not null, drawing_no text default '',
 kind text default '零件', material text default '', status text default '待解析', specifications text default '{}',
 geometry text default '{}', process text default '{}', source_pdf text default '', drawing_pdf text default '',
 drawing_dxf text default '', drawing_dwg text default '', updated_at text not null, quantity double precision default 1,
 owner_id uuid not null default nullif(current_setting('request.jwt.claim.sub',true),'')::uuid references auth.users(id),
 rowid bigint generated always as identity, unique(id,owner_id), unique(id,project_id,owner_id),
 foreign key(project_id,owner_id) references engineering.projects(id,owner_id) on delete cascade,
 foreign key(parent_id,project_id,owner_id) references engineering.parts(id,project_id,owner_id) deferrable initially deferred
);
create table engineering.messages (
 id text primary key, project_id text not null, role text not null, mode text not null, content text not null,
 part_id text default '', created_at text not null,
 owner_id uuid not null default nullif(current_setting('request.jwt.claim.sub',true),'')::uuid references auth.users(id),
 rowid bigint generated always as identity,
 foreign key(project_id,owner_id) references engineering.projects(id,owner_id) on delete cascade
);
create table engineering.resources (
 id text primary key, part_id text not null, name text not null, kind text not null, file_path text not null, created_at text not null,
 owner_id uuid not null default nullif(current_setting('request.jwt.claim.sub',true),'')::uuid references auth.users(id),
 rowid bigint generated always as identity,
 foreign key(part_id,owner_id) references engineering.parts(id,owner_id) on delete cascade
);
create table engineering.mbom_links (
 id text primary key, project_id text not null, parent_id text, child_id text not null,
 quantity double precision not null default 1 check(quantity>0), evidence text default '', confidence text default '待复核',
 owner_id uuid not null default nullif(current_setting('request.jwt.claim.sub',true),'')::uuid references auth.users(id),
 rowid bigint generated always as identity,
 foreign key(project_id,owner_id) references engineering.projects(id,owner_id) on delete cascade,
 foreign key(parent_id,project_id,owner_id) references engineering.parts(id,project_id,owner_id) on delete cascade,
 foreign key(child_id,project_id,owner_id) references engineering.parts(id,project_id,owner_id) on delete cascade
);
create table engineering.engineering_memory (
 id text primary key, category text not null, title text not null, content text not null, source text not null,
 file_path text default '', created_at text not null,
 owner_id uuid not null default nullif(current_setting('request.jwt.claim.sub',true),'')::uuid references auth.users(id),
 rowid bigint generated always as identity
);
create table engineering.jobs (
 id text primary key, project_id text not null, status text not null, total integer not null, completed integer default 0,
 current text default '', errors text default '[]', created_at text not null, updated_at text not null,
 owner_id uuid not null default nullif(current_setting('request.jwt.claim.sub',true),'')::uuid references auth.users(id),
 rowid bigint generated always as identity,
 foreign key(project_id,owner_id) references engineering.projects(id,owner_id) on delete cascade
);
create table engineering.cloud_tasks (
 id text primary key, project_id text not null, operation text not null, payload text not null,
 state text not null default 'queued' check(state in ('queued','running','completed','failed')),
 result text default '{}', error text default '', created_at text not null, updated_at text not null,
 owner_id uuid not null default nullif(current_setting('request.jwt.claim.sub',true),'')::uuid references auth.users(id),
 foreign key(project_id,owner_id) references engineering.projects(id,owner_id) on delete cascade
);
do $$ declare tbl text; begin
 foreach tbl in array array['projects','parts','messages','resources','mbom_links','engineering_memory','jobs','cloud_tasks'] loop
   execute format('alter table engineering.%I enable row level security',tbl);
   execute format('alter table engineering.%I force row level security',tbl);
   execute format('create policy tenant_access on engineering.%I to engineering_app using (owner_id = nullif(current_setting(''request.jwt.claim.sub'',true),'''')::uuid) with check (owner_id = nullif(current_setting(''request.jwt.claim.sub'',true),'''')::uuid)',tbl);
   execute format('create index on engineering.%I(owner_id)',tbl);
 end loop;
end $$;
create index on engineering.parts(project_id);
create index on engineering.messages(project_id,created_at);
create index on engineering.mbom_links(project_id,parent_id);
create index on engineering.resources(part_id);
create index on engineering.cloud_tasks(project_id,state);
grant select,insert,update,delete on all tables in schema engineering to engineering_app;
grant usage,select on all sequences in schema engineering to engineering_app;

insert into storage.buckets(id,name,public,file_size_limit)
values('engineering-private','engineering-private',false,67108864)
on conflict(id) do update set public=false;
-- Objects are only served through the authenticated API. No anonymous/public Storage policies.
