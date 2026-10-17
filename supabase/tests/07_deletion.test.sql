-- Right-to-delete (FR-102): request -> storage purge (Edge path) -> SQL job ->
-- proof; afterwards NO trace of the subject id in any table or storage object.
begin;
\ir _helpers.psql
select plan(26);

-- Scans every table in public/private/storage/auth for a text fragment.
create or replace function tests.tables_containing(p_fragment text)
returns setof text
language plpgsql
as $$
declare
  t record;
  hit boolean;
begin
  for t in
    select n.nspname, c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where n.nspname in ('public', 'private', 'storage', 'auth') and c.relkind in ('r', 'p')
  loop
    execute format('select exists (select 1 from %I.%I x where x::text ilike %L)',
                   t.nspname, t.relname, '%' || p_fragment || '%') into hit;
    if hit then
      return next t.nspname || '.' || t.relname;
    end if;
  end loop;
end
$$;

-- Fixtures ------------------------------------------------------------------
do $$ begin
  update public.feature_flag set enabled = true, approval_ref = 'TEST-KEPK-0' where key = 'dataset_contribution';
  perform tests.make_child('00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-00000000ca07', array['a', 'b', 'c']);
  perform tests.make_child('00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-00000000ca08');
  perform tests.make_child('00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-00000000cb07');
end $$;

select tests.as_user('00000000-0000-4000-8000-0000000000a1');
do $$ begin
  insert into storage.objects (bucket_id, name, owner, metadata) values ('dataset_photos',
    '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-00000000ca07/87000000-0000-4000-8000-000000000001.jpg',
    '00000000-0000-4000-8000-0000000000a1', '{"size": 200000, "mimetype": "image/jpeg"}');
  perform public.sync_events(jsonb_build_array(
    tests.ev('e7000000-0000-4000-8000-000000000001', 'screening_recorded', jsonb_build_object(
      'screening_id', '57000000-0000-4000-8000-000000000001', 'child_id', '00000000-0000-4000-8000-00000000ca07',
      'screened_at', now(), 'model_version', '0.1.0', 'rules_version', '0.1.0', 'level', 'orange')),
    tests.ev('e7000000-0000-4000-8000-000000000002', 'brushing_recorded', jsonb_build_object(
      'brushing_id', '67000000-0000-4000-8000-000000000001', 'child_id', '00000000-0000-4000-8000-00000000ca07',
      'started_at', now(), 'duration_s', 110)),
    tests.ev('e7000000-0000-4000-8000-000000000003', 'referral_created', jsonb_build_object(
      'referral_id', '77000000-0000-4000-8000-000000000001', 'child_id', '00000000-0000-4000-8000-00000000ca07',
      'screening_id', '57000000-0000-4000-8000-000000000001', 'level', 'orange')),
    tests.ev('e7000000-0000-4000-8000-000000000004', 'image_upload_registered', jsonb_build_object(
      'upload_id', '87000000-0000-4000-8000-000000000001', 'child_id', '00000000-0000-4000-8000-00000000ca07',
      'object_path', '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-00000000ca07/87000000-0000-4000-8000-000000000001.jpg'))));
end $$;
select is((select count(*) from public.image_upload)::int + (select count(*) from public.screening)::int
          + (select count(*) from public.referral)::int + (select count(*) from public.brushing_event)::int, 4,
          'fixture: child has screening, brushing, referral and a registered photo');

-- 1. Request ------------------------------------------------------------------
select public.request_deletion('child', '00000000-0000-4000-8000-00000000ca07') as req1 \gset
select is(public.request_deletion('child', '00000000-0000-4000-8000-00000000ca07'), :'req1'::uuid,
          'requesting twice returns the same pending request (idempotent)');
select is((public.sync_events(jsonb_build_array(tests.ev('e7000000-0000-4000-8000-000000000005', 'brushing_recorded',
          jsonb_build_object('brushing_id', '67000000-0000-4000-8000-000000000002',
                             'child_id', '00000000-0000-4000-8000-00000000ca07', 'started_at', now(), 'duration_s', 90)))))
          -> 0 ->> 'code', 'deletion_pending', 'sync for the subject stops as soon as deletion is requested');
select throws_ok($$ select public.request_deletion('account', '00000000-0000-4000-8000-00000000ca07') $$,
                 '22023', null, 'account scope does not take a child id');

select tests.as_user('00000000-0000-4000-8000-0000000000b1');
select throws_ok($$ select public.request_deletion('child', '00000000-0000-4000-8000-00000000ca07') $$,
                 '42501', null, 'B cannot request deletion of A''s child');

-- 2. pg_cron pass: storage objects still exist -> waits --------------------------
select tests.as_owner();
select is(private.run_pending_deletions(), 0, 'cron pass completes nothing while photos remain in Storage');
select results_eq(format($$ select status::text, failure_code from public.deletion_request where id = %L $$, :'req1'),
                  $$ values ('processing'::text, 'awaiting_storage_purge'::text) $$,
                  'request waits for the storage purge');

-- 3. Edge Function path (process-deletion) ---------------------------------------
select tests.as_service();
select is((select storage_paths from public.service_pending_deletions(10) where request_id = :'req1'),
          array['00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-00000000ca07/87000000-0000-4000-8000-000000000001.jpg'],
          'service lists exactly the objects to remove');
-- simulate storage.from('dataset_photos').remove(paths) (the Storage API delete)
select tests.as_owner();
select set_config('storage.allow_delete_query', 'true', true);
delete from storage.objects where bucket_id = 'dataset_photos'
  and name like '%00000000-0000-4000-8000-00000000ca07%';
select set_config('storage.allow_delete_query', 'false', true);
select tests.as_service();
select is(public.service_execute_deletion(:'req1', 1) ->> 'status', 'completed', 'Edge path completes the request');

-- 4. No trace + proof --------------------------------------------------------
select tests.as_owner();
select is_empty($$ select * from tests.tables_containing('00000000-0000-4000-8000-00000000ca07') $$,
                'NO table (public/private/storage/auth) contains the deleted child id');
select results_eq(format($$ select status::text, subject_id, executed_by, storage_objects_deleted,
                            (rows_deleted ->> 'screening')::int, (rows_deleted ->> 'image_upload')::int,
                            (rows_deleted ->> 'consent')::int
                     from public.deletion_request where id = %L $$, :'req1'),
                  $$ values ('completed'::text, null::uuid, 'edge:process-deletion'::text, 1, 1, 1, 3) $$,
                  'proof: status, cleared subject, executor, counts per table and storage');
select ok((select subject_hash ~ '^[0-9a-f]{64}$' and proof_sha256 ~ '^[0-9a-f]{64}$' and completed_at is not null
           from public.deletion_request where id = :'req1'), 'proof: subject_hash, proof_sha256, completed_at');
select ok(exists (select 1 from public.audit_log a join public.deletion_request d on d.subject_hash = a.target_hash
                  where d.id = :'req1' and a.action = 'deletion_completed'),
          'audit_log has a hashed deletion_completed entry linked to the proof');
select ok(exists (select 1 from public.child_profile where id = '00000000-0000-4000-8000-00000000ca08'),
          'other children of the same account are untouched');

select tests.as_user('00000000-0000-4000-8000-0000000000a1');
select is((select status::text from public.deletion_request where id = :'req1'), 'completed',
          'requester can read the completed request (proof) for their records');

-- 5. Account deletion through the pg_cron path --------------------------------
select tests.as_user('00000000-0000-4000-8000-0000000000b1');
do $$ begin
  perform public.sync_events(jsonb_build_array(tests.ev('e7000000-0000-4000-8000-0000000000b1', 'screening_recorded',
    jsonb_build_object('screening_id', '57000000-0000-4000-8000-0000000000b1',
                       'child_id', '00000000-0000-4000-8000-00000000cb07', 'screened_at', now(),
                       'model_version', '0.1.0', 'rules_version', '0.1.0', 'level', 'green'))));
end $$;
select public.request_deletion('account') as req2 \gset
select tests.as_owner();
-- GoTrue audit trail as written by the real auth server (no FK to auth.users):
-- id + e-mail inside the JSON payload. A's entry is the control.
insert into auth.audit_log_entries (instance_id, id, payload, created_at, ip_address) values
  ('00000000-0000-0000-0000-000000000000', gen_random_uuid(), json_build_object(
     'action', 'user_signedup', 'actor_id', '00000000-0000-4000-8000-0000000000b1',
     'actor_username', 'tst-00000000-0000-4000-8000-0000000000b1@example.invalid', 'log_type', 'team',
     'traits', json_build_object('provider', 'email', 'user_id', '00000000-0000-4000-8000-0000000000b1',
                                 'user_email', 'tst-00000000-0000-4000-8000-0000000000b1@example.invalid')), now(), ''),
  ('00000000-0000-0000-0000-000000000000', gen_random_uuid(), json_build_object(
     'action', 'login', 'actor_id', '00000000-0000-4000-8000-0000000000b1', 'log_type', 'account',
     'traits', json_build_object('provider', 'email')), now(), ''),
  ('00000000-0000-0000-0000-000000000000', gen_random_uuid(), json_build_object(
     'action', 'login', 'actor_id', '00000000-0000-4000-8000-0000000000a1', 'log_type', 'account',
     'actor_username', 'tst-00000000-0000-4000-8000-0000000000a1@example.invalid',
     'traits', json_build_object('provider', 'email')), now(), '');
select is((select count(*)::int from auth.audit_log_entries
           where payload::text like '%00000000-0000-4000-8000-0000000000b1%'), 2,
          'fixture: GoTrue audit entries mention the account id/e-mail');
select is(private.run_pending_deletions(), 1, 'cron pass completes the account deletion (no photos)');
select is((select (rows_deleted ->> 'auth_audit_log_entries')::int from public.deletion_request where id = :'req2'), 2,
          'proof counts the purged GoTrue audit entries');
select is((select count(*)::int from auth.audit_log_entries
           where payload::text like '%00000000-0000-4000-8000-0000000000a1%'), 1,
          'other accounts'' GoTrue audit entries are kept');
select ok(not exists (select 1 from auth.users where id = '00000000-0000-4000-8000-0000000000b1'),
          'auth account removed');
select is_empty($$ select * from tests.tables_containing('00000000-0000-4000-8000-0000000000b1') $$,
                'NO table contains the deleted account id');
select is_empty($$ select * from tests.tables_containing('00000000-0000-4000-8000-00000000cb07') $$,
                'NO table contains the id of the account''s child');
select is_empty($$ select * from tests.tables_containing('tst-00000000-0000-4000-8000-0000000000b1@') $$,
                'NO table contains the account e-mail');
select results_eq(format($$ select status::text, requested_by, executed_by, (rows_deleted ->> 'auth_user')::int,
                            (rows_deleted ->> 'user_role')::int, (rows_deleted ->> 'child_profile')::int
                     from public.deletion_request where id = %L $$, :'req2'),
                  $$ values ('completed'::text, null::uuid, 'pg_cron'::text, 1, 1, 1) $$,
                  'account proof: auth user, role and child counted; requester link cleared');
select ok(exists (select 1 from auth.users where id = '00000000-0000-4000-8000-0000000000a1'),
          'other accounts untouched');
select is(private.run_pending_deletions(), 0, 'nothing left to process');

select * from finish();
rollback;
