-- Free-tier quota monitoring (IMPLEMENTATION_PLAN 5.4): access control, metric
-- computation, thresholds, audit, and that the snapshot carries no identifiers.
begin;
\ir _helpers.psql
select plan(24);

create temp table q_base as select private.quota_usage() as v;
grant select on q_base to authenticated, service_role;

create or replace function pg_temp.used(p jsonb, p_metric text)
returns numeric language sql immutable as $$
  select (m ->> 'used')::numeric from jsonb_array_elements(p -> 'metrics') m where m ->> 'metric' = p_metric
$$;
create or replace function pg_temp.status(p jsonb, p_metric text)
returns text language sql immutable as $$
  select m ->> 'status' from jsonb_array_elements(p -> 'metrics') m where m ->> 'metric' = p_metric
$$;

-- ---------------------------------------------------------------- access control
select tests.as_user('00000000-0000-4000-8000-0000000000e1');
select is((select array_agg(m ->> 'metric') from jsonb_array_elements(public.admin_quota_usage() -> 'metrics') m),
          array['db_size', 'storage_size', 'mau', 'auth_email_24h'], 'admin sees the four metrics in order');
select ok(public.admin_quota_usage() ->> 'overall_status' in ('ok', 'warn', 'critical', 'unknown'),
          'overall_status is a known code');
select ok((public.admin_quota_usage() -> 'metrics' -> 0 ->> 'used')::numeric > 0, 'db_size is measured (> 0 MB)');

select tests.as_owner();
select ok((select count(*) from public.audit_log where action = 'quota_read') >= 3,
          'every admin read is audited as quota_read');
select ok((select bool_and(details ? 'overall_status' and not details ? 'metrics')
           from public.audit_log where action = 'quota_read'),
          'audit detail holds only the overall status');

select tests.as_user('00000000-0000-4000-8000-0000000000a1');
select throws_ok($$ select public.admin_quota_usage() $$, '42501', null, 'guardian cannot read quotas');
select throws_ok($$ select public.service_quota_usage() $$, '42501', null, 'guardian cannot call the service RPC');
select tests.as_owner();
do $$ begin perform tests.new_teen('00000000-0000-4000-8000-0000000010f1'); end $$;
select tests.as_user('00000000-0000-4000-8000-0000000010f1');
select throws_ok($$ select public.admin_quota_usage() $$, '42501', null, 'teen cannot read quotas');
select tests.as_anon();
select throws_ok($$ select public.admin_quota_usage() $$, '42501', null, 'anon cannot read quotas');

select tests.as_owner();
create temp table q_audit as select count(*) as n from public.audit_log where action = 'quota_read';
grant select on q_audit to service_role;
select tests.as_service();
select ok(public.service_quota_usage() ? 'metrics', 'service_role gets the snapshot');
select tests.as_owner();
select is((select count(*) from public.audit_log where action = 'quota_read'), (select n from q_audit),
          'service reads are not written to audit_log');

-- ---------------------------------------------------------------- metric inputs
do $$ begin
  -- storage: one 600 MiB object (metadata only) + one with a malformed size (ignored)
  insert into storage.objects (bucket_id, name, metadata) values
    ('manifests', 'tst/quota-600mib.bin', jsonb_build_object('size', 629145600)),
    ('manifests', 'tst/quota-bad.bin', jsonb_build_object('size', 'abc'));
  -- MAU: one sign-in this month counts, one last month does not
  perform tests.new_user('00000000-0000-4000-8000-00000000f001');
  perform tests.new_user('00000000-0000-4000-8000-00000000f002');
  update auth.users set last_sign_in_at = now() where id = '00000000-0000-4000-8000-00000000f001';
  update auth.users set last_sign_in_at = date_trunc('month', now()) - interval '1 day'
   where id = '00000000-0000-4000-8000-00000000f002';
  -- e-mail: 2 sends in the last 24 h count; an old send and a login do not
  insert into auth.audit_log_entries (instance_id, id, payload, created_at) values
    (null, gen_random_uuid(), '{"action": "user_confirmation_requested"}', now() - interval '1 hour'),
    (null, gen_random_uuid(), '{"action": "user_recovery_requested"}', now() - interval '2 hours'),
    (null, gen_random_uuid(), '{"action": "user_recovery_requested"}', now() - interval '2 days'),
    (null, gen_random_uuid(), '{"action": "login"}', now());
end $$;

create temp table q_now as select private.quota_usage() as v;
select is(pg_temp.used((select v from q_now), 'storage_size') - pg_temp.used((select v from q_base), 'storage_size'),
          600.0, 'storage_size sums object sizes in MB (malformed size ignored)');
select is(pg_temp.used((select v from q_now), 'mau') - pg_temp.used((select v from q_base), 'mau'),
          1::numeric, 'mau counts sign-ins of the current month only');
select is(pg_temp.used((select v from q_now), 'auth_email_24h') - pg_temp.used((select v from q_base), 'auth_email_24h'),
          2::numeric, 'auth_email_24h counts e-mail-sending actions of the last 24 h only');

-- ---------------------------------------------------------------- thresholds
update public.app_setting set value = '1' where key = 'quota_db_mb';
select is(pg_temp.status(private.quota_usage(), 'db_size'), 'critical', 'db over its limit -> critical');
select is(private.quota_usage() ->> 'overall_status', 'critical', 'overall status = worst metric');

select is(private.quota_metric('x', 'mb', 69.9, 100, 70, 90) ->> 'status', 'ok', '69.9 % -> ok');
select is(private.quota_metric('x', 'mb', 70, 100, 70, 90) ->> 'status', 'warn', '70 % -> warn (inclusive)');
select is(private.quota_metric('x', 'mb', 90, 100, 70, 90) ->> 'status', 'critical', '90 % -> critical (inclusive)');
select is(private.quota_metric('x', 'mb', 1, 3, 70, 90) ->> 'pct', '33.3', 'pct rounded to one decimal');
select is(private.quota_metric('x', 'mb', null, 100, 70, 90) ->> 'status', 'unknown', 'unmeasured -> unknown');
select is(private.quota_metric('x', 'mb', 5, 0, 70, 90) ->> 'status', 'unknown', 'zero/invalid limit -> unknown');

update public.app_setting set value = '95' where key = 'quota_warn_pct';
update public.app_setting set value = '80' where key = 'quota_critical_pct';
select is((private.quota_usage() ->> 'critical_pct')::int, 95, 'critical_pct is never below warn_pct');

-- ---------------------------------------------------------------- no identifiers
select ok(private.quota_usage()::text !~* '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|@|tst/',
          'snapshot contains no uuid, e-mail or object path');

select * from finish();
rollback;
