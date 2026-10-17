-- ADR 017 amendment (2026-10-18): the brushing proof video is also available to the
-- kader (own roster children, as history), and a kader never gets child accounts.
-- Fixture users: A guardian, B guardian, K1 kader, FV facility_viewer, AD admin (_helpers).
begin;
\ir _helpers.psql
select plan(25);

do $$ begin
  perform tests.make_child('00000000-0000-4000-8000-0000000000c1', '00000000-0000-4000-8000-0000000c7101', array['a', 'b', 'd']);
  perform tests.make_child('00000000-0000-4000-8000-0000000000c1', '00000000-0000-4000-8000-0000000c7102', array['a', 'b']);
  perform tests.make_child('00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000c71a1', array['a', 'b', 'd']);
  perform tests.make_child('00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000c71b1', array['a', 'b', 'd']);
end $$;

-- ------------------------------------------------------------ kader K1 registers a proof
select tests.as_user('00000000-0000-4000-8000-0000000000c1');
select public.sync_events(jsonb_build_array(
  tests.ev('e1700000-0000-4000-8000-000000000001', 'brushing_recorded', jsonb_build_object(
    'brushing_id', '61700000-0000-4000-8000-000000000001', 'child_id', '00000000-0000-4000-8000-0000000c7101',
    'started_at', now() - interval '20 minutes', 'duration_s', 120, 'camera_mode', true)),
  tests.ev('e1700000-0000-4000-8000-000000000002', 'brushing_proof_registered', jsonb_build_object(
    'proof_id', 'b1700000-0000-4000-8000-000000000001', 'child_id', '00000000-0000-4000-8000-0000000c7101',
    'brushing_event_id', '61700000-0000-4000-8000-000000000001', 'size_bytes', 2000000, 'duration_s', 100,
    'mime', 'video/mp4')),
  tests.ev('e1700000-0000-4000-8000-000000000003', 'brushing_recorded', jsonb_build_object(
    'brushing_id', '61700000-0000-4000-8000-000000000002', 'child_id', '00000000-0000-4000-8000-0000000c7102',
    'started_at', now() - interval '10 minutes', 'duration_s', 120)),
  tests.ev('e1700000-0000-4000-8000-000000000004', 'brushing_proof_registered', jsonb_build_object(
    'proof_id', 'b1700000-0000-4000-8000-000000000002', 'child_id', '00000000-0000-4000-8000-0000000c7102',
    'brushing_event_id', '61700000-0000-4000-8000-000000000002', 'size_bytes', 2000000, 'duration_s', 100,
    'mime', 'video/mp4')),
  tests.ev('e1700000-0000-4000-8000-000000000005', 'brushing_recorded', jsonb_build_object(
    'brushing_id', '61700000-0000-4000-8000-0000000000a1', 'child_id', '00000000-0000-4000-8000-0000000c71a1',
    'started_at', now() - interval '10 minutes', 'duration_s', 120)),
  tests.ev('e1700000-0000-4000-8000-000000000006', 'brushing_proof_registered', jsonb_build_object(
    'proof_id', 'b1700000-0000-4000-8000-0000000000a1', 'child_id', '00000000-0000-4000-8000-0000000c71a1',
    'brushing_event_id', '61700000-0000-4000-8000-0000000000a1', 'size_bytes', 2000000, 'duration_s', 100,
    'mime', 'video/mp4')))
)::text as kr \gset
select is((:'kr'::jsonb) -> 1 ->> 'status', 'applied', 'kader registers a proof for an OWN child');
select results_eq($$ select uploaded_by, object_path, status::text from public.brushing_proof
                     where id = 'b1700000-0000-4000-8000-000000000001' $$,
                  $$ values ('00000000-0000-4000-8000-0000000000c1'::uuid,
                             '00000000-0000-4000-8000-0000000000c1/00000000-0000-4000-8000-0000000c7101/b1700000-0000-4000-8000-000000000001.mp4'::text,
                             'uploaded'::text) $$,
                  '... uploaded_by = the kader, path = <kader>/<child>/<proof>.mp4');
select is((:'kr'::jsonb) -> 3 ->> 'code', 'consent_required', 'no consent (d) for that child -> consent_required');
select is((:'kr'::jsonb) -> 5 ->> 'code', 'forbidden', 'a child the kader does not own -> forbidden');

-- storage: the kader uploads to the registered path only
select lives_ok($$ insert into storage.objects (bucket_id, name, owner, metadata) values ('brushing_proofs',
  '00000000-0000-4000-8000-0000000000c1/00000000-0000-4000-8000-0000000c7101/b1700000-0000-4000-8000-000000000001.mp4',
  '00000000-0000-4000-8000-0000000000c1', '{"size": 2000000, "mimetype": "video/mp4"}') $$,
  'kader uploads the clip to its registered path');
select throws_ok($$ insert into storage.objects (bucket_id, name) values ('brushing_proofs',
  '00000000-0000-4000-8000-0000000000c1/00000000-0000-4000-8000-0000000c7101/b1700000-0000-4000-8000-000000000099.mp4') $$,
  '42501', null, 'no upload to an unregistered path');
select throws_ok($$ insert into storage.objects (bucket_id, name) values ('brushing_proofs',
  '00000000-0000-4000-8000-0000000000c1/00000000-0000-4000-8000-0000000c7102/b1700000-0000-4000-8000-000000000002.mp4') $$,
  '42501', null, 'no upload for a child without consent (d)');
select is((select count(*) from storage.objects where bucket_id = 'brushing_proofs')::int, 1,
          'kader can read (sign) the clip of its own child again');

-- history for the kader
select results_eq($$ select id, object_present, status::text from public.list_brushing_proofs('00000000-0000-4000-8000-0000000c7101') $$,
                  $$ values ('b1700000-0000-4000-8000-000000000001'::uuid, true, 'uploaded'::text) $$,
                  'list_brushing_proofs works for the kader owner');
select ok((public.child_history('00000000-0000-4000-8000-0000000c7101') -> 'items') @> '[{"kind": "brushing_proof"}]'::jsonb,
          'child_history shows the proof (and brushing) to the kader');
select is(public.mark_proof_viewed('b1700000-0000-4000-8000-000000000001') ->> 'status', 'viewed', 'kader marks the proof viewed');
select is((select unviewed_proofs from public.list_children() where child_id = '00000000-0000-4000-8000-0000000c7101'), 0,
          'list_children counts it as viewed');
select is(public.my_account() #>> '{capabilities,register_proofs}', 'true', 'my_account: kader may register proofs');
select is(public.my_account() #>> '{capabilities,create_child_accounts}', 'false', 'my_account: kader may NOT create child accounts');
select throws_ok($$ select public.list_brushing_proofs('00000000-0000-4000-8000-0000000c71a1') $$, '42501', null,
                 'kader cannot list proofs of a child it does not own');

-- ------------------------------------------------------------ guardian A never registers proofs
select tests.as_user('00000000-0000-4000-8000-0000000000a1');
select public.sync_events(jsonb_build_array(
  tests.ev('e1700000-0000-4000-8000-0000000000a2', 'brushing_proof_registered', jsonb_build_object(
    'proof_id', 'b1700000-0000-4000-8000-0000000000a2', 'child_id', '00000000-0000-4000-8000-0000000c71a1',
    'brushing_event_id', '61700000-0000-4000-8000-0000000000a1', 'size_bytes', 2000000, 'duration_s', 100,
    'mime', 'video/mp4'))))::text as ga \gset
select is((:'ga'::jsonb) -> 0 ->> 'code', 'forbidden', 'a guardian (not kader) still cannot register proofs');
select is(public.my_account() #>> '{capabilities,register_proofs}', 'false', 'my_account: guardian cannot register proofs');
select is(public.my_account() #>> '{capabilities,create_child_accounts}', 'true', 'my_account: guardian may create child accounts');
select is((select count(*) from storage.objects where bucket_id = 'brushing_proofs')::int, 0,
          'a guardian cannot read the kader''s clips');
select throws_ok($$ select public.list_brushing_proofs('00000000-0000-4000-8000-0000000c7101') $$, '42501', null,
                 'a guardian cannot list the kader''s proofs');

-- ------------------------------------------------------------ facility_viewer / admin: still nothing
select tests.as_user('00000000-0000-4000-8000-0000000000d1');
select is((select count(*) from storage.objects where bucket_id = 'brushing_proofs')::int, 0, 'facility_viewer cannot read clips');
select tests.as_user('00000000-0000-4000-8000-0000000000e1');
select is((select count(*) from storage.objects where bucket_id = 'brushing_proofs')::int, 0, 'admin cannot read clips');

-- ------------------------------------------------------------ the kader deletes; no child accounts for a kader
select tests.as_user('00000000-0000-4000-8000-0000000000c1');
select is(public.delete_brushing_proof('b1700000-0000-4000-8000-000000000001') ->> 'status', 'deleted', 'kader deletes the proof');
select tests.as_owner();
select throws_ok($$ select tests.new_child_user('00000000-0000-4000-8000-0000000d7101', '00000000-0000-4000-8000-0000000000c1',
                      '00000000-0000-4000-8000-0000000c7102', 'anak.tujuh') $$,
                 '22023', 'child_account_invalid', 'no child account can be created for a kader-owned child');
do $$ begin
  insert into public.user_role (user_id, role, facility_code) values ('00000000-0000-4000-8000-0000000000b1', 'kader', 'TST-FAC-1');
end $$;
select throws_ok($$ select tests.new_child_user('00000000-0000-4000-8000-0000000d71b1', '00000000-0000-4000-8000-0000000000b1',
                      '00000000-0000-4000-8000-0000000c71b1', 'anak.b7') $$,
                 '22023', 'child_account_invalid', 'a guardian that also holds the kader role cannot create child accounts');

select * from finish();
rollback;
