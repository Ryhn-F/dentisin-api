-- RLS isolation: guardian A vs B, teen vs guardians, admin only via audited
-- RPC, anon sees nothing. (Kader / facility_viewer were removed by ADR 018.)
begin;
\ir _helpers.psql
select plan(26);

-- Fixtures (owner) ------------------------------------------------------------
do $$
begin
  perform tests.make_child('00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-00000000ca01');
  perform tests.make_child('00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-00000000cb01');
  insert into public.screening (id, child_id, recorded_by, screened_at, model_version, rules_version, level)
  values ('00000000-0000-4000-8000-0000000005a1', '00000000-0000-4000-8000-00000000ca01',
          '00000000-0000-4000-8000-0000000000a1', now(), '1.0.0', '1.0.0', 'green'),
         ('00000000-0000-4000-8000-0000000005b1', '00000000-0000-4000-8000-00000000cb01',
          '00000000-0000-4000-8000-0000000000b1', now(), '1.0.0', '1.0.0', 'orange');

  -- Teen T with its self profile
  perform tests.new_teen('00000000-0000-4000-8000-0000000001f1');
  insert into public.child_profile (id, owner_id, birth_year, birth_month, is_self)
  values ('00000000-0000-4000-8000-00000000cf01', '00000000-0000-4000-8000-0000000001f1', extract(year from now())::int - 16, 1, true);
end
$$;

-- Guardian A -----------------------------------------------------------------
select tests.as_user('00000000-0000-4000-8000-0000000000a1');
select is((select count(*) from public.child_profile)::int, 1, 'A sees exactly one child (own)');
select is_empty($$ select 1 from public.child_profile where id = '00000000-0000-4000-8000-00000000cb01' $$,
                'A cannot read B''s child');
select is((select count(*) from public.screening)::int, 1, 'A sees only own screening');
select is_empty($$ select 1 from public.consent where child_id = '00000000-0000-4000-8000-00000000cb01' $$,
                'A cannot read B''s consent');
select is((select count(*) from public.guardian)::int, 1, 'A sees only own guardian row');
select is((select count(*) from public.user_role)::int, 1, 'A sees only own role row');
select throws_ok($$
  insert into public.screening (id, child_id, recorded_by, screened_at, model_version, rules_version, level)
  values ('00000000-0000-4000-8000-00000000dead', '00000000-0000-4000-8000-00000000cb01',
          '00000000-0000-4000-8000-0000000000a1', now(), '1.0.0', '1.0.0', 'green') $$,
  '42501', null, 'A cannot write a screening for B''s child');
select throws_ok($$
  insert into public.screening (id, child_id, recorded_by, screened_at, model_version, rules_version, level)
  values ('00000000-0000-4000-8000-00000000dea2', '00000000-0000-4000-8000-00000000ca01',
          '00000000-0000-4000-8000-0000000000b1', now(), '1.0.0', '1.0.0', 'green') $$,
  '42501', null, 'A cannot write as someone else (recorded_by spoofing)');
select lives_ok($$ update public.child_profile set birth_year = 2010 where id = '00000000-0000-4000-8000-00000000cb01' $$,
                'update of B''s child by A runs ...');
select is((select count(*) from public.audit_log)::int, 0, 'A cannot read audit_log');
select is((select count(*) from public.app_setting)::int, 0, 'A cannot read app_setting');
select throws_ok($$ insert into public.child_profile (id, owner_id) values ('00000000-0000-4000-8000-00000000ca99', '00000000-0000-4000-8000-0000000000b1') $$,
                 '42501', null, 'A cannot create a child owned by B');

select tests.as_owner();
select is((select birth_year from public.child_profile where id = '00000000-0000-4000-8000-00000000cb01')::int, 2017,
          '... but B''s child is unchanged');

-- Guardian B mirrors ------------------------------------------------------------
select tests.as_user('00000000-0000-4000-8000-0000000000b1');
select is((select array_agg(id) from public.child_profile), array['00000000-0000-4000-8000-00000000cb01'::uuid],
          'B sees only own child');

-- Teen T ----------------------------------------------------------------------
select tests.as_user('00000000-0000-4000-8000-0000000001f1');
select is((select array_agg(id) from public.child_profile), array['00000000-0000-4000-8000-00000000cf01'::uuid],
          'teen sees only its self profile');
select tests.as_user('00000000-0000-4000-8000-0000000000a1');
select is_empty($$ select 1 from public.child_profile where id = '00000000-0000-4000-8000-00000000cf01' $$,
                'a guardian cannot read a teen''s self profile');

-- admin ------------------------------------------------------------------------
select tests.as_user('00000000-0000-4000-8000-0000000000e1');
select is((select count(*) from public.child_profile)::int, 0, 'admin has no direct row access to children');
select throws_ok($$ select public.admin_get_child_record('00000000-0000-4000-8000-00000000ca01', null) $$,
                 '22023', null, 'admin read requires a reason code');
select is((public.admin_get_child_record('00000000-0000-4000-8000-00000000ca01', 'support_ticket') -> 'child' ->> 'birth_year'),
          '2017', 'admin can read one child through the audited RPC');
select ok(exists (select 1 from public.audit_log where action = 'admin_child_read'), '... and the read is audited');
select is_empty($$ select 1 from public.audit_log a where a::text like '%00000000-0000-4000-8000-00000000ca01%' $$,
                'audit_log stores no raw child id');

-- anon -------------------------------------------------------------------------
select tests.as_anon();
select throws_ok($$ select * from public.child_profile $$, '42501', null, 'anon: child_profile denied');
select throws_ok($$ select * from public.screening $$, '42501', null, 'anon: screening denied');
select throws_ok($$ select * from public.feature_flag $$, '42501', null, 'anon: feature_flag denied');
select throws_ok($$ select public.sync_events('[]'::jsonb) $$, '42501', null, 'anon: sync_events denied');
select is(public.keepalive(), 1, 'anon: keepalive() returns 1');

select tests.as_owner();
select * from finish();
rollback;
