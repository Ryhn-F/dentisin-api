-- Roles cannot be self-assigned; admin paths are audited; audit_log is append-only.
begin;
\ir _helpers.psql
select plan(19);

-- Signup with role hints in metadata still yields only 'guardian'
insert into auth.users (id, aud, role, email, raw_app_meta_data, raw_user_meta_data)
values ('00000000-0000-4000-8000-0000000000f1', 'authenticated', 'authenticated', 'tst-sneaky@example.invalid',
        '{"role": "admin", "roles": ["admin"]}', '{"role": "admin", "app_role": "kader"}');
select results_eq($$ select role::text from public.user_role where user_id = '00000000-0000-4000-8000-0000000000f1' $$,
                  $$ values ('guardian'::text) $$, 'signup metadata cannot request a role');

select tests.as_user('00000000-0000-4000-8000-0000000000a1');
select throws_ok($$ insert into public.user_role (user_id, role) values ('00000000-0000-4000-8000-0000000000a1', 'admin') $$,
                 '42501', null, 'guardian cannot INSERT a role for self');
select throws_ok($$ update public.user_role set role = 'admin' where user_id = '00000000-0000-4000-8000-0000000000a1' $$,
                 '42501', null, 'guardian cannot UPDATE own role');
select throws_ok($$ delete from public.user_role where user_id = '00000000-0000-4000-8000-0000000000a1' $$,
                 '42501', null, 'guardian cannot DELETE roles');
select throws_ok($$ select public.admin_grant_role('00000000-0000-4000-8000-0000000000a1', 'admin') $$,
                 '42501', null, 'guardian cannot call admin_grant_role');
select throws_ok($$ select public.admin_set_feature_flag('dataset_contribution', true, 'KEPK-1') $$,
                 '42501', null, 'guardian cannot flip feature flags via RPC');
select throws_ok($$ update public.feature_flag set enabled = true $$,
                 '42501', null, 'guardian cannot update feature_flag directly');
select throws_ok($$ select public.admin_storage_usage() $$, '42501', null, 'guardian cannot read quota monitor');
select throws_ok($$ select * from public.service_pending_deletions(10) $$, '42501', null,
                 'guardian cannot call service_role RPCs');

-- Forged JWT claims do not grant app roles (roles come from user_role only)
select tests.set_claims('00000000-0000-4000-8000-0000000000a1', 'authenticated');
select set_config('request.jwt.claims',
  '{"sub": "00000000-0000-4000-8000-0000000000a1", "role": "authenticated", "app_metadata": {"role": "admin"}, "user_role": "admin"}', true);
select is(private.is_admin(), false, 'JWT app_metadata/user claims do not make an admin');
select throws_ok($$ select private.hash_ref('00000000-0000-4000-8000-0000000000a1') $$, '42501', null,
                 'clients cannot compute audit hashes');

-- Admin path --------------------------------------------------------------------
select tests.as_user('00000000-0000-4000-8000-0000000000e1');
select lives_ok($$ select public.admin_grant_role('00000000-0000-4000-8000-0000000000b1', 'admin') $$,
                'admin can grant admin to an account without children');
select ok(exists (select 1 from public.audit_log where action = 'role_granted'
                    and target_hash ~ '^[0-9a-f]{64}$'), 'role grant is audited with a hashed target');
select throws_ok($$ select public.admin_set_feature_flag('dataset_contribution', true, null) $$,
                 '22023', null, 'enabling dataset_contribution requires an approval reference (KEPK)');
select throws_ok($$ select public.admin_revoke_role('00000000-0000-4000-8000-0000000000e1', 'admin') $$,
                 '22023', null, 'admin cannot remove own admin role');

select tests.as_owner();
do $$ begin perform tests.make_child('00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-00000000ca01'); end $$;
select tests.as_user('00000000-0000-4000-8000-0000000000e1');
select throws_ok($$ select public.admin_grant_role('00000000-0000-4000-8000-0000000000a1', 'admin') $$,
                 '22023', null, 'accounts owning child data cannot become admin (separation of duties)');

-- audit_log append-only ---------------------------------------------------------
select tests.as_owner();
select throws_ok($$ update public.audit_log set action = 'tampered' $$, '42501', null,
                 'audit_log: UPDATE blocked even for the owner');
select throws_ok($$ delete from public.audit_log $$, '42501', null, 'audit_log: DELETE blocked even for the owner');
select tests.as_service();
select throws_ok($$ delete from public.audit_log $$, '42501', null, 'audit_log: service_role cannot delete');

select tests.as_owner();
select * from finish();
rollback;
