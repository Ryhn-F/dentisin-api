-- RLS isolation: guardian A vs B, kader vs other kader, facility_viewer
-- aggregates-only, admin only via audited RPC, anon sees nothing.
begin;
\ir _helpers.psql
select plan(38);

-- Fixtures (owner) ------------------------------------------------------------
do $$
declare
  i int;
  cid uuid;
begin
  perform tests.make_child('00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-00000000ca01');
  perform tests.make_child('00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-00000000cb01');
  insert into public.screening (id, child_id, recorded_by, screened_at, model_version, rules_version, level)
  values ('00000000-0000-4000-8000-0000000005a1', '00000000-0000-4000-8000-00000000ca01',
          '00000000-0000-4000-8000-0000000000a1', now(), '1.0.0', '1.0.0', 'green'),
         ('00000000-0000-4000-8000-0000000005b1', '00000000-0000-4000-8000-00000000cb01',
          '00000000-0000-4000-8000-0000000000b1', now(), '1.0.0', '1.0.0', 'orange');

  insert into public.class_roster (id, kader_id, label, facility_code) values
    ('00000000-0000-4000-8000-0000000f00c1', '00000000-0000-4000-8000-0000000000c1', 'Kelas 4A', 'TST-FAC-1'),
    ('00000000-0000-4000-8000-0000000f00c2', '00000000-0000-4000-8000-0000000000c2', 'Kelas 5B', 'TST-FAC-2');
  -- K1: 8 children in TST-FAC-1 (6 green + 2 yellow this month); K2: 1 child
  for i in 1..8 loop
    cid := ('00000000-0000-4000-8000-0000c1c1' || lpad(i::text, 4, '0'))::uuid;
    perform tests.make_child('00000000-0000-4000-8000-0000000000c1', cid);
    insert into public.class_roster_member (roster_id, child_id, member_label)
    values ('00000000-0000-4000-8000-0000000f00c1', cid, 'A-' || i);
    insert into public.screening (id, child_id, recorded_by, screened_at, model_version, rules_version, level)
    values (('00000000-0000-4000-8000-00005c1c' || lpad(i::text, 4, '0'))::uuid, cid,
            '00000000-0000-4000-8000-0000000000c1', date_trunc('month', now()) + interval '1 hour',
            '1.0.0', '1.0.0', case when i <= 6 then 'green' else 'yellow' end::public.triage_level);
  end loop;
  perform tests.make_child('00000000-0000-4000-8000-0000000000c2', '00000000-0000-4000-8000-0000c2c20001');
  insert into public.class_roster_member (roster_id, child_id, member_label)
  values ('00000000-0000-4000-8000-0000000f00c2', '00000000-0000-4000-8000-0000c2c20001', 'B-1');
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
select throws_ok($$ insert into public.class_roster (kader_id, label) values ('00000000-0000-4000-8000-0000000000a1', 'Kelas 1') $$,
                 '42501', null, 'non-kader cannot create a roster');
select throws_ok($$ insert into public.child_profile (id, owner_id) values ('00000000-0000-4000-8000-00000000ca99', '00000000-0000-4000-8000-0000000000b1') $$,
                 '42501', null, 'A cannot create a child owned by B');

select tests.as_owner();
select is((select birth_year from public.child_profile where id = '00000000-0000-4000-8000-00000000cb01')::int, 2017,
          '... but B''s child is unchanged');

-- Guardian B mirrors ------------------------------------------------------------
select tests.as_user('00000000-0000-4000-8000-0000000000b1');
select is((select array_agg(id) from public.child_profile), array['00000000-0000-4000-8000-00000000cb01'::uuid],
          'B sees only own child');

-- Kader K1 ---------------------------------------------------------------------
select tests.as_user('00000000-0000-4000-8000-0000000000c1');
select is((select count(*) from public.child_profile)::int, 8, 'K1 sees the 8 children of own roster');
select is_empty($$ select 1 from public.child_profile where id in ('00000000-0000-4000-8000-0000c2c20001',
                  '00000000-0000-4000-8000-00000000ca01') $$, 'K1 cannot see K2''s or a parent''s child');
select is((select count(*) from public.class_roster)::int, 1, 'K1 sees only own roster');
select is((select count(*) from public.class_roster_member)::int, 8, 'K1 sees only own roster members');
select throws_ok($$ insert into public.class_roster_member (roster_id, child_id, member_label)
                    values ('00000000-0000-4000-8000-0000000f00c1', '00000000-0000-4000-8000-00000000ca01', 'A-99') $$,
                 '42501', null, 'K1 cannot pull a parent''s child into own roster');
select throws_ok($$ insert into public.class_roster_member (roster_id, child_id, member_label)
                    values ('00000000-0000-4000-8000-0000000f00c2', '00000000-0000-4000-8000-0000c1c10001', 'A-98') $$,
                 '42501', null, 'K1 cannot add members to K2''s roster');
select throws_ok($$ insert into public.class_roster (kader_id, label, facility_code)
                    values ('00000000-0000-4000-8000-0000000000c1', 'Kelas 6', 'TST-FAC-2') $$,
                 '42501', null, 'K1 cannot tag a roster with another facility');
select throws_ok($$ select * from public.facility_screening_summary(current_date, current_date) $$,
                 '42501', null, 'kader cannot call facility aggregates');

select tests.as_user('00000000-0000-4000-8000-0000000000c2');
select is((select array_agg(id) from public.child_profile), array['00000000-0000-4000-8000-0000c2c20001'::uuid],
          'K2 sees only own roster child');

-- facility_viewer ------------------------------------------------------------
select tests.as_user('00000000-0000-4000-8000-0000000000d1');
select is((select count(*) from public.child_profile)::int
        + (select count(*) from public.screening)::int
        + (select count(*) from public.consent)::int
        + (select count(*) from public.referral)::int
        + (select count(*) from public.brushing_event)::int
        + (select count(*) from public.image_upload)::int
        + (select count(*) from public.class_roster)::int
        + (select count(*) from public.class_roster_member)::int, 0,
          'facility_viewer sees ZERO row-level child data');
select results_eq(
  $$ select facility_code, level::text, screenings, suppressed
     from public.facility_screening_summary(current_date, current_date) $$,
  $$ values ('TST-FAC-1'::text, 'yellow'::text, null::int, true), ('TST-FAC-1', 'green', 6, false) $$,
  'aggregates: own facility only, cells < 5 suppressed');
select throws_ok($$ select * from public.facility_screening_summary(current_date, current_date + 800) $$,
                 '22023', null, 'aggregate range is bounded');

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
select ok((select count(*) from public.facility_screening_summary(current_date, current_date)) > 0,
          'admin may read all-facility aggregates');

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
