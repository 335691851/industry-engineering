-- Completed outputs survive a function invocation; tenant isolation matches tasks.
alter table engineering.cloud_tasks add column if not exists slices integer not null default 0;
alter table engineering.cloud_tasks add constraint cloud_tasks_owner_key unique(id,owner_id);
create table engineering.cloud_activities (
 task_id text not null,
 activity_key text not null,
 result text not null,
 created_at text not null,
 owner_id uuid not null default nullif(current_setting('request.jwt.claim.sub',true),'')::uuid,
 primary key(task_id,activity_key),
 foreign key(task_id,owner_id) references engineering.cloud_tasks(id,owner_id) on delete cascade
);
alter table engineering.cloud_activities enable row level security;
alter table engineering.cloud_activities force row level security;
create policy owner_only on engineering.cloud_activities for all to engineering_app
 using(owner_id=nullif(current_setting('request.jwt.claim.sub',true),'')::uuid)
 with check(owner_id=nullif(current_setting('request.jwt.claim.sub',true),'')::uuid);
grant select,insert,update,delete on engineering.cloud_activities to engineering_app;
