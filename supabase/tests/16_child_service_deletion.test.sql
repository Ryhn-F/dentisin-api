-- ADR 017 §2.2/2.7: service_role functions for child-login (resolve, throttle),
-- child users of a guardian, right-to-delete covering child accounts, proofs
-- (Storage) and requests, and the unchanged global guarantees.
begin;
\ir _helpers.psql
select plan(39);

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

do $$ begin
  perform tests.make_child('00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000c6101', array['a', 'b', 'd']);
  perform tests.make_child('00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000c61b1');
  perform tests.new_child_user('00000000-0000-4000-8000-0000000d6101', '00000000-0000-4000-8000-0000000000a1',
                               '00000000-0000-4000-8000-0000000c6101', 'anak.enam');
  perform tests.new_child_user('00000000-0000-4000-8000-0000000d61b1', '00000000-0000-4000-8000-0000000000b1',
                               '00000000-0000-4000-8000-0000000c61b1', 'anak.b');
end $$;

-- ------------------------------------------------------------ child-login resolve
select tests.as_service();
select results_eq($$ select * from public.service_resolve_child_login(
                       'tst-00000000-0000-4000-8000-0000000000a1@example.invalid', 'anak.enam') $$,
                  $$ values ('00000000-0000-4000-8000-0000000d6101'::uuid,
                             'child-00000000-0000-4000-8000-0000000c6101@child.dentisai.invalid'::text, false) $$,
                  'resolve: (parent e-mail, username) -> child user + synthetic e-mail');
select is((select count(*) from public.service_resolve_child_login(
             'TST-00000000-0000-4000-8000-0000000000A1@EXAMPLE.INVALID', '  ANAK.ENAM '))::int, 1,
          'resolve is case- and whitespace-insensitive');
select is_empty($$ select * from public.service_resolve_child_login(
                     'tst-00000000-0000-4000-8000-0000000000a1@example.invalid', 'tidak.ada') $$,
                'unknown username -> zero rows');
select is_empty($$ select * from public.service_resolve_child_login(
                     'tst-00000000-0000-4000-8000-0000000000b1@example.invalid', 'anak.enam') $$,
                'username of another guardian -> zero rows');
select tests.as_owner();
update public.child_account set disabled_at = now() where user_id = '00000000-0000-4000-8000-0000000d6101';
select tests.as_service();
select is((select disabled from public.service_resolve_child_login(
             'tst-00000000-0000-4000-8000-0000000000a1@example.invalid', 'anak.enam')), true,
          'disabled child account is reported as disabled');
select tests.as_owner();
update public.child_account set disabled_at = null where user_id = '00000000-0000-4000-8000-0000000d6101';
select tests.as_user('00000000-0000-4000-8000-0000000000a1');
select throws_ok($$ select * from public.service_resolve_child_login('x@example.invalid', 'anak.enam') $$,
                 '42501', null, 'clients cannot resolve child logins');
select tests.as_anon();
select throws_ok($$ select * from public.service_resolve_child_login('x@example.invalid', 'anak.enam') $$,
                 '42501', null, 'anon cannot resolve child logins');

-- ------------------------------------------------------------ throttling
select tests.as_service();
select is(public.service_child_login_check(repeat('ab', 32), 3, 900), true, 'fresh key: login allowed');
select public.service_child_login_record(repeat('ab', 32), false, 900) from generate_series(1, 3);
select is(public.service_child_login_check(repeat('ab', 32), 3, 900), false, 'after 3 failures (max 3): blocked');
select public.service_child_login_record(repeat('ab', 32), true, 900);
select is(public.service_child_login_check(repeat('ab', 32), 3, 900), true, 'a successful login clears the counter');
select throws_ok($$ select public.service_child_login_check('tst-parent@example.invalid|127.0.0.1|xxxx', 3, 900) $$,
                 '22023', null, 'raw e-mail/IP keys are refused (hash only)');
select is(public.service_child_login_check(repeat('ab', 32)), true, 'defaults come from app_setting');
select tests.as_owner();
insert into public.child_login_attempt (key_hash, window_start, attempts) values
  (repeat('ab', 32), now() - interval '2000 seconds', 100),
  (repeat('cd', 32), now() - interval '2 days', 1);
select tests.as_service();
select is(public.service_child_login_check(repeat('ab', 32), 3, 900), true, 'attempts outside the window do not count');
select is((public.service_retention_sweep_db() ->> 'child_login_attempt_deleted')::int, 1,
          'daily retention purges attempt rows older than a day');
select tests.as_user('00000000-0000-4000-8000-0000000000a1');
select throws_ok($$ select public.service_child_login_check(repeat('ab', 32), 3, 900) $$, '42501', null,
                 'clients cannot call the throttle');
select tests.as_service();
select results_eq($$ select * from public.service_child_users_of('00000000-0000-4000-8000-0000000000a1') $$,
                  $$ values ('00000000-0000-4000-8000-0000000c6101'::uuid, '00000000-0000-4000-8000-0000000d6101'::uuid) $$,
                  'service_child_users_of lists the guardian''s child accounts');

-- ------------------------------------------------------------ deletion (child scope)
select tests.as_user('00000000-0000-4000-8000-0000000d6101');
select is((select array_agg(e ->> 'status') from jsonb_array_elements(public.sync_events(jsonb_build_array(
            tests.ev('e1600000-0000-4000-8000-000000000001', 'brushing_recorded', jsonb_build_object(
              'brushing_id', '61600000-0000-4000-8000-000000000001', 'child_id', '00000000-0000-4000-8000-0000000c6101',
              'started_at', now() - interval '5 minutes', 'duration_s', 120)),
            tests.ev('e1600000-0000-4000-8000-000000000002', 'brushing_proof_registered', jsonb_build_object(
              'proof_id', 'b1600000-0000-4000-8000-000000000001', 'child_id', '00000000-0000-4000-8000-0000000c6101',
              'brushing_event_id', '61600000-0000-4000-8000-000000000001', 'size_bytes', 3000000, 'duration_s', 90,
              'mime', 'video/mp4')),
            tests.ev('e1600000-0000-4000-8000-000000000003', 'screening_requested', jsonb_build_object(
              'request_id', 'a1600000-0000-4000-8000-000000000001', 'child_id', '00000000-0000-4000-8000-0000000c6101',
              'reason_code', 'toothache'))))) e),
          array['applied', 'applied', 'applied'], 'fixture: brushing, proof and request from the child account');
do $$ begin
  insert into storage.objects (bucket_id, name, owner, metadata) values ('brushing_proofs',
    '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-0000000c6101/b1600000-0000-4000-8000-000000000001.mp4',
    '00000000-0000-4000-8000-0000000d6101', '{"size": 3000000, "mimetype": "video/mp4"}');
end $$;
select tests.as_owner();
insert into auth.audit_log_entries (instance_id, id, payload, created_at, ip_address) values
  ('00000000-0000-0000-0000-000000000000', gen_random_uuid(), json_build_object(
     'action', 'login', 'actor_id', '00000000-0000-4000-8000-0000000d6101',
     'actor_username', 'child-00000000-0000-4000-8000-0000000c6101@child.dentisai.invalid', 'log_type', 'account'), now(), ''),
  ('00000000-0000-0000-0000-000000000000', gen_random_uuid(), json_build_object(
     'action', 'login', 'actor_id', '00000000-0000-4000-8000-0000000000a1', 'log_type', 'account'), now(), '');

select tests.as_user('00000000-0000-4000-8000-0000000000a1');
select public.request_deletion('child', '00000000-0000-4000-8000-0000000c6101') as req1 \gset
select tests.as_user('00000000-0000-4000-8000-0000000d6101');
select is((public.sync_events(jsonb_build_array(tests.ev('e1600000-0000-4000-8000-000000000004', 'brushing_recorded',
          jsonb_build_object('brushing_id', '61600000-0000-4000-8000-000000000002', 'child_id', '00000000-0000-4000-8000-0000000c6101',
                             'started_at', now() - interval '1 minute', 'duration_s', 100))))) -> 0 ->> 'code',
          'deletion_pending', 'child account sync stops as soon as the child''s deletion is requested');
select tests.as_service();
select is((select disabled from public.service_resolve_child_login(
             'tst-00000000-0000-4000-8000-0000000000a1@example.invalid', 'anak.enam')), true,
          'child login reports disabled while the deletion is pending');
select tests.as_owner();
select is(private.run_pending_deletions(), 0, 'cron pass waits while a proof object remains in Storage');
select tests.as_service();
select results_eq(format($$ select proof_paths, child_user_ids from public.service_deletion_targets(%L) $$, :'req1'),
                  $$ values (array['00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-0000000c6101/b1600000-0000-4000-8000-000000000001.mp4']::text[],
                             array['00000000-0000-4000-8000-0000000d6101']::uuid[]) $$,
                  'service_deletion_targets: proof objects + child Auth users of the request');
select is((select storage_paths from public.service_pending_deletions(10) where request_id = :'req1'), '{}'::text[],
          'service_pending_deletions keeps its contract (dataset_photos paths only)');
select tests.as_owner();
select set_config('storage.allow_delete_query', 'true', true);
delete from storage.objects where bucket_id = 'brushing_proofs' and name like '%00000000-0000-4000-8000-0000000c6101%';
select set_config('storage.allow_delete_query', 'false', true);
select tests.as_service();
select is(public.service_execute_deletion(:'req1', 1) ->> 'status', 'completed', 'deletion completes after the purge');
select tests.as_owner();
select is_empty($$ select * from tests.tables_containing('00000000-0000-4000-8000-0000000c6101') $$,
                'NO table contains the deleted child id (incl. its synthetic e-mail)');
select is_empty($$ select * from tests.tables_containing('00000000-0000-4000-8000-0000000d6101') $$,
                'NO table contains the child account id');
select results_eq(format($$ select (rows_deleted ->> 'screening_request')::int, (rows_deleted ->> 'brushing_proof')::int,
                                   (rows_deleted ->> 'child_account')::int, (rows_deleted ->> 'child_auth_user')::int,
                                   (rows_deleted ->> 'auth_audit_log_entries')::int
                            from public.deletion_request where id = %L $$, :'req1'),
                  $$ values (1, 1, 1, 1, 1) $$, 'proof counts requests, proofs, child account, Auth user, GoTrue entries');
select ok(exists (select 1 from auth.users where id = '00000000-0000-4000-8000-0000000000a1')
          and exists (select 1 from auth.audit_log_entries where payload::text like '%00000000-0000-4000-8000-0000000000a1%'),
          'the guardian and the guardian''s GoTrue entries stay');

-- ------------------------------------------------------------ deletion (account scope)
select tests.as_user('00000000-0000-4000-8000-0000000000b1');
select public.request_deletion('account') as req2 \gset
select tests.as_owner();
select is(private.run_pending_deletions(), 1, 'account deletion completes in SQL (no objects)');
select is_empty($$ select * from tests.tables_containing('00000000-0000-4000-8000-0000000000b1') $$,
                'NO table contains the deleted guardian id');
select is_empty($$ select * from tests.tables_containing('00000000-0000-4000-8000-0000000c61b1') $$,
                'NO table contains the guardian''s child id');
select is_empty($$ select * from tests.tables_containing('00000000-0000-4000-8000-0000000d61b1') $$,
                'NO table contains the guardian''s child account id');
select is((select (rows_deleted ->> 'child_auth_user')::int from public.deletion_request where id = :'req2'), 1,
          'account proof counts the deleted child Auth user');

-- ------------------------------------------------------------ guarantees
select is_empty($$ select p.proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                   where n.nspname = 'public' and has_function_privilege('anon', p.oid, 'EXECUTE')
                     and p.proname <> 'keepalive' $$, 'anon still executes only keepalive()');
select is_empty($$ select n.nspname || '.' || c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
                   where n.nspname in ('public', 'private') and c.relkind in ('r', 'p')
                     and not (c.relrowsecurity and c.relforcerowsecurity) $$,
                'every table (incl. the new ones) has RLS ENABLED and FORCED');
select is_empty($$ select t || ':' || p from unnest(array['child_account', 'screening_request', 'brushing_proof', 'child_login_attempt']) t,
                        unnest(array['UPDATE', 'DELETE']) p
                   where has_table_privilege('authenticated', 'public.' || t, p)
                   union all
                   select 'child_login_attempt:' || p from unnest(array['SELECT', 'INSERT']) p
                   where has_table_privilege('authenticated', 'public.child_login_attempt', p) $$,
                'authenticated cannot update/delete the new tables nor touch child_login_attempt');
select is_empty($$ select p.proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                   where n.nspname = 'public' and p.proname like 'service\_%'
                     and has_function_privilege('authenticated', p.oid, 'EXECUTE') $$,
                'no service_* function is executable by authenticated');
select is_empty($$ select n.nspname || '.' || p.proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                   where n.nspname in ('public', 'private') and p.prosecdef
                     and not coalesce(p.proconfig @> array['search_path=""'], false) $$,
                'every SECURITY DEFINER function pins search_path');
select throws_ok($$ delete from public.audit_log $$, '42501', null, 'audit_log stays append-only');
select is_empty($$ select table_name from information_schema.columns
                   where table_schema = 'public' and column_name ~* 'user_?name' and table_name <> 'child_account' $$,
                'the username exists only in child_account');

select * from finish();
rollback;
