-- ADR 017: signup trigger (guardian / teen / bogus / child via app_metadata),
-- teen self-profile rules, my_account(), role and deletion guards.
begin;
\ir _helpers.psql
select plan(39);

-- Fixtures (owner): A owns three children (no accounts yet)
do $$ begin
  perform tests.make_child('00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000c1101');
  perform tests.make_child('00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000c1102');
  perform tests.make_child('00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000c1103');
  perform tests.make_child('00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000c11b1');
end $$;

-- ------------------------------------------------------------ signup trigger
select is((select account_type::text from public.guardian where id = '00000000-0000-4000-8000-0000000000a1'),
          'guardian', 'plain signUp -> guardian row with account_type guardian');

select tests.new_teen('00000000-0000-4000-8000-000000007e01');
select is((select account_type::text from public.guardian where id = '00000000-0000-4000-8000-000000007e01'),
          'teen', 'signUp with account_type teen -> teen');
select results_eq($$ select role::text from public.user_role where user_id = '00000000-0000-4000-8000-000000007e01' $$,
                  $$ values ('guardian'::text) $$, 'a teen gets only the guardian role');

insert into auth.users (id, aud, role, email, raw_app_meta_data, raw_user_meta_data) values
  ('00000000-0000-4000-8000-0000000f1101', 'authenticated', 'authenticated', 'tst-bogus@example.invalid',
   '{"provider": "email"}', '{"account_type": "admin"}'),
  ('00000000-0000-4000-8000-0000000f1102', 'authenticated', 'authenticated', 'tst-sneaky-child@example.invalid',
   '{"provider": "email"}', '{"account_type": "child", "guardian_id": "00000000-0000-4000-8000-0000000000a1", "child_id": "00000000-0000-4000-8000-0000000c1103", "username": "sneaky"}');
select is((select account_type::text from public.guardian where id = '00000000-0000-4000-8000-0000000f1101'),
          'guardian', 'unknown account_type value -> guardian');
select ok((select account_type = 'guardian' from public.guardian where id = '00000000-0000-4000-8000-0000000f1102')
          and not exists (select 1 from public.child_account where user_id = '00000000-0000-4000-8000-0000000f1102')
          and not exists (select 1 from public.user_role where user_id = '00000000-0000-4000-8000-0000000f1102' and role = 'child'),
          'a client cannot become a child account through user_metadata (signUp)');

select tests.new_child_user('00000000-0000-4000-8000-0000000d1101', '00000000-0000-4000-8000-0000000000a1',
                            '00000000-0000-4000-8000-0000000c1101', 'budi.k');
select results_eq($$ select child_id, guardian_id, username, disabled_at from public.child_account
                     where user_id = '00000000-0000-4000-8000-0000000d1101' $$,
                  $$ values ('00000000-0000-4000-8000-0000000c1101'::uuid, '00000000-0000-4000-8000-0000000000a1'::uuid,
                             'budi.k'::text, null::timestamptz) $$,
                  'child via app_metadata -> child_account row');
select results_eq($$ select role::text from public.user_role where user_id = '00000000-0000-4000-8000-0000000d1101' $$,
                  $$ values ('child'::text) $$, 'a child account gets ONLY the child role');
select ok(not exists (select 1 from public.guardian where id = '00000000-0000-4000-8000-0000000d1101'),
          'a child account gets no guardian row');

-- GoTrue admin API path: INSERT first, app_metadata written by an UPDATE afterwards
insert into auth.users (id, aud, role, email, raw_app_meta_data, raw_user_meta_data) values
  ('00000000-0000-4000-8000-0000000d1102', 'authenticated', 'authenticated',
   'child-00000000-0000-4000-8000-0000000c1102@child.dentisai.invalid', '{"provider": "email"}', '{}');
update auth.users
   set raw_app_meta_data = '{"provider": "email", "account_type": "child", "guardian_id": "00000000-0000-4000-8000-0000000000a1", "child_id": "00000000-0000-4000-8000-0000000c1102", "username": "sari"}'
 where id = '00000000-0000-4000-8000-0000000d1102';
select ok(exists (select 1 from public.child_account where user_id = '00000000-0000-4000-8000-0000000d1102'
                    and child_id = '00000000-0000-4000-8000-0000000c1102')
          and not exists (select 1 from public.guardian where id = '00000000-0000-4000-8000-0000000d1102')
          and (select array_agg(role::text) from public.user_role where user_id = '00000000-0000-4000-8000-0000000d1102') = array['child'],
          'app_metadata written after the INSERT converts the fresh account into a child account');

select throws_ok($$ select tests.new_child_user('00000000-0000-4000-8000-0000000d1109', '00000000-0000-4000-8000-0000000000a1',
                    '00000000-0000-4000-8000-0000000c11b1', 'nyasar') $$,
                 '22023', null, 'child account for a child the guardian does not own is refused');
select throws_ok($$ select tests.new_child_user('00000000-0000-4000-8000-0000000d1109', '00000000-0000-4000-8000-0000000000a1',
                    '00000000-0000-4000-8000-0000000c1103', 'Budi Besar') $$,
                 '22023', null, 'invalid username is refused');
select throws_ok($$ select tests.new_child_user('00000000-0000-4000-8000-0000000d1109', '00000000-0000-4000-8000-0000000000a1',
                    '00000000-0000-4000-8000-0000000c1103', 'budi.k') $$,
                 '23505', null, 'username is unique per guardian');
update public.app_setting set value = '2' where key = 'max_child_accounts_per_guardian';
select throws_ok($$ select tests.new_child_user('00000000-0000-4000-8000-0000000d1109', '00000000-0000-4000-8000-0000000000a1',
                    '00000000-0000-4000-8000-0000000c1103', 'tiga') $$,
                 '22023', null, 'max_child_accounts_per_guardian is enforced');
update public.app_setting set value = '6' where key = 'max_child_accounts_per_guardian';
select throws_ok($$ update auth.users set raw_app_meta_data = jsonb_build_object('account_type', 'child',
                      'guardian_id', '00000000-0000-4000-8000-0000000000b1', 'child_id', '00000000-0000-4000-8000-0000000c11b1',
                      'username', 'ambil.alih') where id = '00000000-0000-4000-8000-0000000000a1' $$,
                 '42501', null, 'an established guardian cannot be converted into a child account');
update auth.users set last_sign_in_at = now() where id = '00000000-0000-4000-8000-0000000d1101';
select ok((select last_login_at is not null from public.child_account where user_id = '00000000-0000-4000-8000-0000000d1101'),
          'last_sign_in_at is mirrored into child_account.last_login_at');

-- ------------------------------------------------------------ teen self profile
select tests.as_user('00000000-0000-4000-8000-000000007e01');
select is(public.ensure_self_profile('00000000-0000-4000-8000-0000000c7e01',
                                     (extract(year from now())::int - 16)::smallint, 3::smallint),
          '00000000-0000-4000-8000-0000000c7e01'::uuid, 'teen creates the self profile');
select ok((select is_self and owner_id = '00000000-0000-4000-8000-000000007e01' from public.child_profile
           where id = '00000000-0000-4000-8000-0000000c7e01'), 'self profile is_self = true, owned by the teen');
select is(public.ensure_self_profile('00000000-0000-4000-8000-0000000c7e99',
                                     (extract(year from now())::int - 16)::smallint, 3::smallint),
          '00000000-0000-4000-8000-0000000c7e01'::uuid, 'ensure_self_profile is idempotent (returns the existing id)');
select throws_ok($$ select public.ensure_self_profile('00000000-0000-4000-8000-0000000c7e99',
                    (extract(year from now())::int - 6)::smallint, 1::smallint) $$,
                 '22023', null, 'younger than teen_min_age is refused');
select throws_ok($$ select public.ensure_self_profile('00000000-0000-4000-8000-0000000c7e99',
                    (extract(year from now())::int - 22)::smallint, 1::smallint) $$,
                 '22023', null, 'older than teen_max_age is refused');
select throws_ok($$ select public.ensure_self_profile('00000000-0000-4000-8000-0000000c7e99',
                    (extract(year from now())::int - 15)::smallint, null) $$,
                 '22023', null, 'unknown birth month is judged strictly (could still be 14)');
select is((public.sync_events(jsonb_build_array(tests.ev('e1100000-0000-4000-8000-000000000001', 'consent_changed',
          jsonb_build_object('child_id', '00000000-0000-4000-8000-0000000c7e02', 'consent_type', 'a', 'action', 'grant',
                             'text_version', 'v1'))))) -> 0 ->> 'code',
          'forbidden', 'a teen cannot create any other child profile');
select is((select array_agg(e ->> 'status') from jsonb_array_elements(public.sync_events(jsonb_build_array(
            tests.ev('e1100000-0000-4000-8000-000000000002', 'consent_changed', jsonb_build_object(
              'child_id', '00000000-0000-4000-8000-0000000c7e01', 'consent_type', 'a', 'action', 'grant', 'text_version', 'v1')),
            tests.ev('e1100000-0000-4000-8000-000000000003', 'consent_changed', jsonb_build_object(
              'child_id', '00000000-0000-4000-8000-0000000c7e01', 'consent_type', 'b', 'action', 'grant', 'text_version', 'v1')),
            tests.ev('e1100000-0000-4000-8000-000000000004', 'brushing_recorded', jsonb_build_object(
              'brushing_id', '61100000-0000-4000-8000-000000000001', 'child_id', '00000000-0000-4000-8000-0000000c7e01',
              'started_at', now() - interval '1 hour', 'duration_s', 120))))) e),
          array['applied', 'applied', 'applied'], 'teen syncs consent + data for the self profile');
select is((public.sync_events(jsonb_build_array(tests.ev('e1100000-0000-4000-8000-000000000005', 'child_upserted',
          jsonb_build_object('child_id', '00000000-0000-4000-8000-0000000c7e01',
                             'birth_year', extract(year from now())::int - 5, 'birth_month', 1))))) -> 0 ->> 'code',
          'schema_invalid', 'self profile birth data stays inside the teen window');
select is((public.sync_events(jsonb_build_array(tests.ev('e1100000-0000-4000-8000-000000000006', 'child_upserted',
          jsonb_build_object('child_id', '00000000-0000-4000-8000-0000000c7e01', 'allergies', '[]'::jsonb))))) -> 0 ->> 'code',
          'schema_invalid', 'self profile cannot drop its birth year');
select is((public.my_account() ->> 'account_type') || '|' || (public.my_account() ->> 'self_child_id'),
          'teen|00000000-0000-4000-8000-0000000c7e01', 'my_account: teen + self_child_id');

select tests.as_owner();
select throws_ok($$ select tests.new_child_user('00000000-0000-4000-8000-0000000d1109', '00000000-0000-4000-8000-000000007e01',
                    '00000000-0000-4000-8000-0000000c7e01', 'diriku') $$,
                 '22023', null, 'no child account for a teen or a self profile');
select throws_ok($$ insert into public.child_profile (id, owner_id, birth_year, is_self)
                    values ('00000000-0000-4000-8000-0000000c11a9', '00000000-0000-4000-8000-0000000000a1', 2012, true) $$,
                 '42501', null, 'only teen accounts can own a self profile');

select tests.new_teen('00000000-0000-4000-8000-000000007e03');
select tests.as_user('00000000-0000-4000-8000-000000007e03');
select throws_ok($$ select public.ensure_self_profile('00000000-0000-4000-8000-0000000c7e30',
                    (extract(year from now())::int - 14)::smallint, 1::smallint) $$,
                 '22023', null, 'age 14 is refused: under 15 needs a guardian account');
select is(public.ensure_self_profile('00000000-0000-4000-8000-0000000c7e30',
                                     (extract(year from now())::int - 15)::smallint, 1::smallint),
          '00000000-0000-4000-8000-0000000c7e30'::uuid, 'exactly 15 is accepted');
select tests.as_owner();
select tests.new_teen('00000000-0000-4000-8000-000000007e02');
select tests.as_user('00000000-0000-4000-8000-000000007e02');
select throws_ok($$ select public.ensure_self_profile('00000000-0000-4000-8000-0000000c1101',
                    (extract(year from now())::int - 16)::smallint, 3::smallint) $$,
                 '42501', null, 'an id used by someone else''s child gives the uniform forbidden');

select tests.as_user('00000000-0000-4000-8000-0000000000a1');
select throws_ok($$ select public.ensure_self_profile('00000000-0000-4000-8000-0000000c11a8', 2012::smallint, 3::smallint) $$,
                 '42501', null, 'a guardian cannot create a self profile');
select ok(public.my_account() ->> 'account_type' = 'guardian' and (public.my_account() ->> 'child_accounts')::int = 2
          and public.my_account() -> 'limits' ? 'proof_max_bytes',
          'my_account: guardian, number of child accounts, limits');

select tests.as_user('00000000-0000-4000-8000-0000000d1101');
select results_eq($$ select x ->> 'account_type', x -> 'child_account' ->> 'child_id', x -> 'child_account' ->> 'username',
                            (x -> 'child_account' ->> 'disabled')::boolean, (x -> 'child_account' ->> 'sync_allowed')::boolean,
                            (x -> 'child_account' ->> 'proof_allowed')::boolean
                     from (select public.my_account() as x) t $$,
                  $$ values ('child'::text, '00000000-0000-4000-8000-0000000c1101'::text, 'budi.k'::text, false, true, false) $$,
                  'my_account: child facts (proof not allowed without consent d)');
select throws_ok($$ select public.request_deletion('account') $$, '42501', null, 'a child account cannot delete itself');
select throws_ok($$ select public.request_deletion('child', '00000000-0000-4000-8000-0000000c1101') $$, '42501', null,
                 'a child account cannot request deletion of its child');

select tests.as_user('00000000-0000-4000-8000-0000000000e1');
select throws_ok($$ select public.admin_grant_role('00000000-0000-4000-8000-0000000000b1', 'child') $$,
                 '22023', null, 'admin cannot grant the child role');
select throws_ok($$ select public.admin_grant_role('00000000-0000-4000-8000-0000000d1101', 'admin') $$,
                 '22023', null, 'admin cannot give a child account another role');

select tests.as_anon();
select throws_ok($$ select public.my_account() $$, '42501', null, 'anon cannot call my_account');

select tests.as_owner();
select * from finish();
rollback;
