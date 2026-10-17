-- ADR 018: kader / class mode / facility aggregates are removed; a TEEN registers,
-- uploads and watches again the brushing proof videos of its OWN self profile.
-- Proof registration is allowed only for (1) an enabled child account (own child)
-- and (2) a teen (own self profile). Guardians and admins never register proofs;
-- only the owner of the child reads them.
-- Fixture users: A guardian, B guardian, AD admin (_helpers); T teen, T2 teen,
-- CA child account of A's child (created below).
begin;
\ir _helpers.psql
select plan(37);

do $$ begin
  perform tests.new_teen('00000000-0000-4000-8000-000000007f01');
  perform tests.new_teen('00000000-0000-4000-8000-000000007f02');
  insert into public.child_profile (id, owner_id, birth_year, birth_month, is_self)
  values ('00000000-0000-4000-8000-0000000c7f02', '00000000-0000-4000-8000-000000007f02', 2012, 2, true);
  insert into public.consent (child_id, granted_by, consent_type, text_version, granted_at)
  select '00000000-0000-4000-8000-0000000c7f02', '00000000-0000-4000-8000-000000007f02', t::public.consent_type,
         'test-v1', now() - interval '1 day'
  from unnest(array['a', 'b', 'd']) t;
  perform tests.make_child('00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000c71a1', array['a', 'b', 'd']);
  perform tests.new_child_user('00000000-0000-4000-8000-0000000d71a1', '00000000-0000-4000-8000-0000000000a1',
                               '00000000-0000-4000-8000-0000000c71a1', 'anak.tujuh');
  perform tests.make_child('00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000c71b1', array['a', 'b', 'd']);
end $$;

-- ------------------------------------------------------------ teen T: self profile + consents
select tests.as_user('00000000-0000-4000-8000-000000007f01');
select is(public.ensure_self_profile('00000000-0000-4000-8000-0000000c7f01', 2012::smallint, 3::smallint),
          '00000000-0000-4000-8000-0000000c7f01'::uuid, 'teen creates its self profile');
select public.sync_events(jsonb_build_array(
  tests.ev('e1700000-0000-4000-8000-000000000001', 'consent_changed', jsonb_build_object(
    'child_id', '00000000-0000-4000-8000-0000000c7f01', 'consent_type', 'a', 'action', 'grant', 'text_version', 'v1')),
  tests.ev('e1700000-0000-4000-8000-000000000002', 'consent_changed', jsonb_build_object(
    'child_id', '00000000-0000-4000-8000-0000000c7f01', 'consent_type', 'b', 'action', 'grant', 'text_version', 'v1')),
  tests.ev('e1700000-0000-4000-8000-000000000003', 'brushing_recorded', jsonb_build_object(
    'brushing_id', '61700000-0000-4000-8000-000000000001', 'child_id', '00000000-0000-4000-8000-0000000c7f01',
    'started_at', now() - interval '20 minutes', 'duration_s', 120, 'camera_mode', true)),
  tests.ev('e1700000-0000-4000-8000-000000000004', 'brushing_proof_registered', jsonb_build_object(
    'proof_id', 'b1700000-0000-4000-8000-000000000001', 'child_id', '00000000-0000-4000-8000-0000000c7f01',
    'brushing_event_id', '61700000-0000-4000-8000-000000000001', 'size_bytes', 2000000, 'duration_s', 100,
    'mime', 'video/mp4')))
)::text as t1 \gset
select is((select array_agg(x ->> 'status' order by o) from jsonb_array_elements(:'t1'::jsonb) with ordinality j(x, o))::text,
          '{applied,applied,applied,rejected}', 'teen grants (a), (b) and records brushing for its self profile');
select is((:'t1'::jsonb) -> 3 ->> 'code', 'consent_required', 'no consent (d) yet -> consent_required');

select public.sync_events(jsonb_build_array(
  tests.ev('e1700000-0000-4000-8000-000000000005', 'consent_changed', jsonb_build_object(
    'child_id', '00000000-0000-4000-8000-0000000c7f01', 'consent_type', 'd', 'action', 'grant', 'text_version', 'v1')),
  tests.ev('e1700000-0000-4000-8000-000000000006', 'brushing_proof_registered', jsonb_build_object(
    'proof_id', 'b1700000-0000-4000-8000-000000000001', 'child_id', '00000000-0000-4000-8000-0000000c7f01',
    'brushing_event_id', '61700000-0000-4000-8000-000000000001', 'size_bytes', 2000000, 'duration_s', 100,
    'mime', 'video/mp4')),
  tests.ev('e1700000-0000-4000-8000-000000000007', 'consent_changed', jsonb_build_object(
    'child_id', '00000000-0000-4000-8000-0000000c7f01', 'consent_type', 'c', 'action', 'grant', 'text_version', 'v1',
    'method', 'paper_via_kader')),
  tests.ev('e1700000-0000-4000-8000-000000000008', 'brushing_proof_registered', jsonb_build_object(
    'proof_id', 'b1700000-0000-4000-8000-0000000000a1', 'child_id', '00000000-0000-4000-8000-0000000c71a1',
    'brushing_event_id', '61700000-0000-4000-8000-000000000001', 'size_bytes', 2000000, 'duration_s', 100,
    'mime', 'video/mp4')),
  tests.ev('e1700000-0000-4000-8000-000000000009', 'brushing_proof_registered', jsonb_build_object(
    'proof_id', 'b1700000-0000-4000-8000-0000000007f2', 'child_id', '00000000-0000-4000-8000-0000000c7f02',
    'brushing_event_id', '61700000-0000-4000-8000-000000000001', 'size_bytes', 2000000, 'duration_s', 100,
    'mime', 'video/mp4')))
)::text as t2 \gset
select is((:'t2'::jsonb) -> 0 ->> 'status', 'applied', 'teen grants consent (d) for its self profile');
select is((:'t2'::jsonb) -> 1 ->> 'status', 'applied', 'teen registers a proof for its self profile');
select results_eq($$ select uploaded_by, object_path, status::text from public.brushing_proof
                     where id = 'b1700000-0000-4000-8000-000000000001' $$,
                  $$ values ('00000000-0000-4000-8000-000000007f01'::uuid,
                             '00000000-0000-4000-8000-000000007f01/00000000-0000-4000-8000-0000000c7f01/b1700000-0000-4000-8000-000000000001.mp4'::text,
                             'uploaded'::text) $$,
                  '... uploaded_by = the teen, path = <teen>/<self>/<proof>.mp4');
select is((:'t2'::jsonb) -> 2 ->> 'code', 'forbidden', 'paper_via_kader consent (retired) -> forbidden');
select is((:'t2'::jsonb) -> 3 ->> 'code', 'forbidden', 'a teen cannot register a proof for another user''s child');
select is((:'t2'::jsonb) -> 4 ->> 'code', 'forbidden', 'a teen cannot register a proof for another teen''s self profile');

-- storage: the teen uploads to the registered path only, and reads its own clip
select lives_ok($$ insert into storage.objects (bucket_id, name, owner, metadata) values ('brushing_proofs',
  '00000000-0000-4000-8000-000000007f01/00000000-0000-4000-8000-0000000c7f01/b1700000-0000-4000-8000-000000000001.mp4',
  '00000000-0000-4000-8000-000000007f01', '{"size": 2000000, "mimetype": "video/mp4"}') $$,
  'teen uploads the clip to its registered path');
select throws_ok($$ insert into storage.objects (bucket_id, name) values ('brushing_proofs',
  '00000000-0000-4000-8000-000000007f01/00000000-0000-4000-8000-0000000c7f01/b1700000-0000-4000-8000-000000000099.mp4') $$,
  '42501', null, 'no upload to an unregistered path');
select is((select count(*) from storage.objects where bucket_id = 'brushing_proofs')::int, 1,
          'teen can read (sign) its own clip again');

-- history for the teen
select results_eq($$ select id, object_present, status::text from public.list_brushing_proofs('00000000-0000-4000-8000-0000000c7f01') $$,
                  $$ values ('b1700000-0000-4000-8000-000000000001'::uuid, true, 'uploaded'::text) $$,
                  'list_brushing_proofs works for the teen');
select ok((public.child_history('00000000-0000-4000-8000-0000000c7f01') -> 'items') @> '[{"kind": "brushing_proof"}]'::jsonb,
          'child_history shows the proof to the teen');
select is(public.mark_proof_viewed('b1700000-0000-4000-8000-000000000001') ->> 'status', 'viewed', 'teen marks its proof viewed');
select is((select unviewed_proofs from public.list_children() where child_id = '00000000-0000-4000-8000-0000000c7f01'), 0,
          'list_children counts it as viewed');
select results_eq($$ select x ->> 'account_type', x #>> '{capabilities,register_proofs}', x #>> '{capabilities,create_child_accounts}'
                     from (select public.my_account() as x) t $$,
                  $$ values ('teen'::text, 'true'::text, 'false'::text) $$,
                  'my_account: a teen may register proofs, may NOT create child accounts');
select tests.as_owner();
update public.feature_flag set enabled = false where key = 'brushing_proof_video';
select tests.as_user('00000000-0000-4000-8000-000000007f01');
select is(public.my_account() #>> '{capabilities,register_proofs}', 'false', 'my_account: flag off -> teen cannot register proofs');
select tests.as_owner();
update public.feature_flag set enabled = true where key = 'brushing_proof_video';

-- ------------------------------------------------------------ guardian A never registers proofs
select tests.as_user('00000000-0000-4000-8000-0000000000a1');
select public.sync_events(jsonb_build_array(
  tests.ev('e1700000-0000-4000-8000-0000000000a2', 'brushing_recorded', jsonb_build_object(
    'brushing_id', '61700000-0000-4000-8000-0000000000a2', 'child_id', '00000000-0000-4000-8000-0000000c71a1',
    'started_at', now() - interval '30 minutes', 'duration_s', 120)),
  tests.ev('e1700000-0000-4000-8000-0000000000a3', 'brushing_proof_registered', jsonb_build_object(
    'proof_id', 'b1700000-0000-4000-8000-0000000000a2', 'child_id', '00000000-0000-4000-8000-0000000c71a1',
    'brushing_event_id', '61700000-0000-4000-8000-0000000000a2', 'size_bytes', 2000000, 'duration_s', 100,
    'mime', 'video/mp4'))))::text as ga \gset
select is((:'ga'::jsonb) -> 1 ->> 'code', 'forbidden', 'a guardian cannot register a proof for its own child');
select results_eq($$ select x ->> 'account_type', x #>> '{capabilities,register_proofs}', x #>> '{capabilities,create_child_accounts}'
                     from (select public.my_account() as x) t $$,
                  $$ values ('guardian'::text, 'false'::text, 'true'::text) $$,
                  'my_account: a guardian may create child accounts, may NOT register proofs');
select is((select count(*) from storage.objects where bucket_id = 'brushing_proofs')::int, 0,
          'guardian A cannot read the teen''s clip');

-- ------------------------------------------------------------ guardian B / admin: nothing
select tests.as_user('00000000-0000-4000-8000-0000000000b1');
select is((select count(*) from storage.objects where bucket_id = 'brushing_proofs')::int, 0, 'guardian B cannot read the teen''s clip');
select throws_ok($$ select public.list_brushing_proofs('00000000-0000-4000-8000-0000000c7f01') $$, '42501', null,
                 'guardian B cannot list the teen''s proofs');
select tests.as_user('00000000-0000-4000-8000-0000000000e1');
select is((select count(*) from storage.objects where bucket_id = 'brushing_proofs')::int, 0, 'admin cannot read the teen''s clip');
select throws_ok($$ select public.list_brushing_proofs('00000000-0000-4000-8000-0000000c7f01') $$, '42501', null,
                 'admin cannot list the teen''s proofs');

-- ------------------------------------------------------------ the child account path still works
select tests.as_user('00000000-0000-4000-8000-0000000d71a1');
select public.sync_events(jsonb_build_array(
  tests.ev('e1700000-0000-4000-8000-0000000000c1', 'brushing_proof_registered', jsonb_build_object(
    'proof_id', 'b1700000-0000-4000-8000-0000000000c1', 'child_id', '00000000-0000-4000-8000-0000000c71a1',
    'brushing_event_id', '61700000-0000-4000-8000-0000000000a2', 'size_bytes', 2000000, 'duration_s', 100,
    'mime', 'video/mp4'))))::text as ca \gset
select is((:'ca'::jsonb) -> 0 ->> 'status', 'applied', 'a child account still registers proofs for its own child');
select results_eq($$ select x ->> 'account_type', x #>> '{capabilities,register_proofs}', x #>> '{capabilities,create_child_accounts}'
                     from (select public.my_account() as x) t $$,
                  $$ values ('child'::text, 'true'::text, 'false'::text) $$,
                  'my_account: child account register_proofs = proof_allowed, no child accounts');

-- ------------------------------------------------------------ the teen deletes its proof
select tests.as_user('00000000-0000-4000-8000-000000007f01');
select is(public.delete_brushing_proof('b1700000-0000-4000-8000-000000000001') ->> 'status', 'deleted', 'teen deletes its proof');
select is_empty($$ select 1 from public.list_brushing_proofs('00000000-0000-4000-8000-0000000c7f01') $$,
                '... and it is hidden at once');

-- ------------------------------------------------------------ kader / roster / facility are gone
select tests.as_owner();
select ok(to_regclass('public.class_roster') is null and to_regclass('public.class_roster_member') is null,
          'class_roster and class_roster_member are dropped');
select ok(to_regprocedure('public.facility_screening_summary(date, date)') is null,
          'facility_screening_summary is dropped');
select is_empty($$ select p.oid::regprocedure::text from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                   where n.nspname in ('public', 'private') and p.prosrc ilike '%class_roster%' $$,
                'no function body references class_roster');
select is_empty($$ select p.oid::regprocedure::text from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                   where n.nspname in ('public', 'private') and p.prosrc ~* '(kader|facility_viewer)'
                     and p.proname not in ('admin_grant_role', 'sync_consent_changed') $$,
                'only admin_grant_role / sync_consent_changed mention the retired values (to refuse them)');
select is((select count(*)::int from public.user_role where role in ('kader', 'facility_viewer')), 0,
          'no account holds a retired role');
select tests.as_user('00000000-0000-4000-8000-0000000000e1');
select throws_ok($$ select public.admin_grant_role('00000000-0000-4000-8000-0000000000b1', 'kader', 'TST-FAC-1') $$,
                 '22023', 'role_not_available', 'admin cannot grant the retired kader role');
select throws_ok($$ select public.admin_grant_role('00000000-0000-4000-8000-0000000000b1', 'facility_viewer', 'TST-FAC-1') $$,
                 '22023', 'role_not_available', 'admin cannot grant the retired facility_viewer role');
select tests.as_owner();
select lives_ok($$ select tests.new_child_user('00000000-0000-4000-8000-0000000d71b1', '00000000-0000-4000-8000-0000000000b1',
                     '00000000-0000-4000-8000-0000000c71b1', 'anak.b7') $$,
                'provisioning has no kader refusal any more: guardian B gets a child account');

select * from finish();
rollback;
