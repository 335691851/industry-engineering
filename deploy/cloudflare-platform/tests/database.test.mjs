import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { PGlite } from '@electric-sql/pglite';

test('migration executes with tenant RLS and cross-project foreign keys', async () => {
  const db = new PGlite();
  try {
    await db.exec(`create schema auth; create table auth.users(id uuid primary key);
      create role anon; create role authenticated;
      create schema storage; create table storage.buckets(id text primary key,name text,public boolean,file_size_limit bigint);
      insert into auth.users values('aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'),('bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb');`);
    await db.exec(await readFile('../../supabase/migrations/20260928005941_engineering_cloud_business.sql','utf8'));
    await db.exec(await readFile('../../supabase/migrations/20260928020000_vercel_task_dispatch.sql','utf8'));
    await db.exec(await readFile('../../supabase/migrations/20260928040000_hobby_activities.sql','utf8'));
    await db.exec(`set role engineering_app; set search_path=engineering,public;
      select set_config('request.jwt.claim.sub','aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa',false);
      insert into projects(id,name,source_path,created_at) values('a','甲项目','object://a/source.pdf','now');
      insert into parts(id,project_id,name,updated_at) values('a-part','a','零件甲','now');
      insert into mbom_links(id,project_id,child_id) values('a-link','a','a-part');
      insert into messages(id,project_id,role,mode,content,created_at) values('m','a','user','agent','hello','now');
      insert into cloud_tasks(id,project_id,operation,payload,created_at,updated_at) values('t','a','request','{}','now','now');
      insert into cloud_activities(task_id,activity_key,result,created_at) values('t','step','{}','now');`);
    assert.equal((await db.query('select count(*)::int as n from parts')).rows[0].n,1);
    assert.equal((await db.query('select workflow_run_id from cloud_tasks')).rows[0].workflow_run_id,'');
    await db.exec(`select set_config('request.jwt.claim.sub','bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb',false)`);
    for (const table of ['projects','parts','mbom_links','messages','cloud_tasks','cloud_activities']) {
      assert.equal((await db.query(`select count(*)::int as n from ${table}`)).rows[0].n,0,table);
    }
    assert.equal((await db.query("update parts set name='stolen' where id='a-part' returning id")).rows.length,0);
    await assert.rejects(db.exec(`insert into projects(id,name,source_path,created_at,owner_id)
      values('fake','fake','','now','aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa')`),/row-level security/);
    await assert.rejects(db.exec("insert into parts(id,project_id,name,updated_at) values('cross','a','foreign','now')"),/foreign key/);
    await db.exec(`reset role; set role authenticated`);
    await assert.rejects(db.query('select * from engineering.projects'),/permission denied/);
  } finally { await db.close(); }
});
