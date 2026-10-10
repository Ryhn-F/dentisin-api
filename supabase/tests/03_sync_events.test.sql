-- sync_events: idempotency per event_id, deterministic replays, validation,
-- ownership and batch limits.
begin;
\ir _helpers.psql
select plan(30);

do $$ begin
  perform tests.make_child('00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-00000000ca01');
  perform tests.make_child('00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-00000000cb01');
end $$;

select jsonb_build_array(
  tests.ev('e0000000-0000-4000-8000-000000000001', 'screening_recorded', jsonb_build_object(
    'screening_id', '50000000-0000-4000-8000-000000000001', 'child_id', '00000000-0000-4000-8000-00000000ca01',
    'screened_at', now() - interval '1 hour', 'model_version', '0.1.0', 'rules_version', '0.1.0',
    'level', 'yellow', 'red_flag', false,
    'findings', '[{"class": "caries_permanent", "confidence": 0.71, "box": [0.1, 0.2, 0.05, 0.04]}]'::jsonb)),
  tests.ev('e0000000-0000-4000-8000-000000000002', 'brushing_recorded', jsonb_build_object(
    'brushing_id', '60000000-0000-4000-8000-000000000001', 'child_id', '00000000-0000-4000-8000-00000000ca01',
    'started_at', now() - interval '2 hours', 'duration_s', 120, 'spirit_score', 77, 'slot', 'morning', 'camera_mode', true)),
  tests.ev('e0000000-0000-4000-8000-000000000003', 'referral_created', jsonb_build_object(
    'referral_id', '70000000-0000-4000-8000-000000000001', 'child_id', '00000000-0000-4000-8000-00000000ca01',
    'screening_id', '50000000-0000-4000-8000-000000000001', 'level', 'yellow',
    'reason_codes', '["caries_primary"]'::jsonb, 'referred_at', now() - interval '1 hour')),
  tests.ev('e0000000-0000-4000-8000-000000000004', 'child_upserted', jsonb_build_object(
    'child_id', '00000000-0000-4000-8000-00000000ca01', 'birth_year', 2016, 'birth_month', 7,
    'allergies', '["mint"]'::jsonb))
)::text as batch1 \gset

select tests.as_user('00000000-0000-4000-8000-0000000000a1');
select public.sync_events(:'batch1'::jsonb)::text as r1 \gset

select is((select count(*) from jsonb_array_elements(:'r1'::jsonb) e where e ->> 'status' = 'applied')::int, 4,
          'first submission: 4/4 applied');
select is((select array_agg((e ->> 'index')::int order by (e ->> 'index')::int) from jsonb_array_elements(:'r1'::jsonb) e),
          array[0, 1, 2, 3], 'results carry the input index');

select public.sync_events(:'batch1'::jsonb)::text as r2 \gset
select is(:'r2'::jsonb, :'r1'::jsonb, 'replaying the same batch returns an identical result');
select public.sync_events(:'batch1'::jsonb)::text as r3 \gset
select is(:'r3'::jsonb, :'r1'::jsonb, '... deterministically (third replay)');

select is((select count(*) from public.screening where child_id = '00000000-0000-4000-8000-00000000ca01')::int, 1,
          'no duplicate screening after replays');
select is((select count(*) from public.brushing_event)::int, 1, 'no duplicate brushing_event');
select is((select count(*) from public.referral)::int, 1, 'no duplicate referral');
select is((select count(*) from public.sync_event)::int, 4, 'ledger holds exactly 4 events for A');
select is((select birth_month from public.child_profile where id = '00000000-0000-4000-8000-00000000ca01')::int, 7,
          'child_upserted applied');

-- Mixed batch: one replay + one new event
select public.sync_events(jsonb_build_array(
  (:'batch1'::jsonb) -> 0,
  tests.ev('e0000000-0000-4000-8000-000000000005', 'brushing_recorded', jsonb_build_object(
    'brushing_id', '60000000-0000-4000-8000-000000000002', 'child_id', '00000000-0000-4000-8000-00000000ca01',
    'started_at', now() - interval '30 minutes', 'duration_s', 95)))
)::text as r4 \gset
select is(((:'r4'::jsonb) -> 0) - 'index', ((:'r1'::jsonb) -> 0) - 'index','replayed event inside a new batch keeps its result');
select is((:'r4'::jsonb) -> 1 ->> 'status', 'applied', 'new event in the same batch is applied');

-- Same event_id, different payload -> conflict, nothing written
select is((public.sync_events(jsonb_build_array(tests.ev('e0000000-0000-4000-8000-000000000002', 'brushing_recorded',
          jsonb_build_object('brushing_id', '60000000-0000-4000-8000-000000000009',
                             'child_id', '00000000-0000-4000-8000-00000000ca01',
                             'started_at', now() - interval '3 hours', 'duration_s', 10))))) -> 0 ->> 'code',
          'event_id_conflict', 'same event_id with a different payload is rejected');

-- Same entity id under a new event_id -> entity_conflict
select is((public.sync_events(jsonb_build_array(tests.ev('e0000000-0000-4000-8000-000000000006', 'brushing_recorded',
          jsonb_build_object('brushing_id', '60000000-0000-4000-8000-000000000001',
                             'child_id', '00000000-0000-4000-8000-00000000ca01',
                             'started_at', now() - interval '3 hours', 'duration_s', 10))))) -> 0 ->> 'code',
          'entity_conflict', 'reusing an entity id under a new event_id is rejected');

-- Validation ------------------------------------------------------------------
select is((public.sync_events(jsonb_build_array(tests.ev('e0000000-0000-4000-8000-000000000010', 'child_upserted',
          jsonb_build_object('child_id', '00000000-0000-4000-8000-00000000ca01', 'birth_year', 2016,
                             'full_name', 'Synthetic Name'))))) -> 0 ->> 'code',
          'schema_invalid', 'unknown payload keys (e.g. a name) are rejected');
select is((public.sync_events(jsonb_build_array(tests.ev('e0000000-0000-4000-8000-000000000011', 'photo_uploaded',
          jsonb_build_object('child_id', '00000000-0000-4000-8000-00000000ca01'))))) -> 0 ->> 'code',
          'unknown_event_type', 'unknown event types are rejected');
select is(public.sync_events('[{"event_id": "not-a-uuid", "type": "child_upserted", "payload": {}}]'::jsonb) -> 0,
          '{"index": 0, "event_id": null, "status": "rejected", "code": "schema_invalid"}'::jsonb,
          'invalid event_id is rejected without being stored');
select is((public.sync_events(jsonb_build_array(tests.ev('e0000000-0000-4000-8000-000000000012', 'screening_recorded',
          jsonb_build_object('screening_id', '50000000-0000-4000-8000-000000000012',
                             'child_id', '00000000-0000-4000-8000-00000000ca01',
                             'screened_at', now(), 'model_version', '0.1.0', 'rules_version', '0.1.0', 'level', 'green',
                             'findings', '[{"class": "caries_permanent", "note": "free text"}]'::jsonb))))) -> 0 ->> 'code',
          'schema_invalid', 'findings with extra keys are rejected');
select is((public.sync_events(jsonb_build_array(tests.ev('e0000000-0000-4000-8000-000000000013', 'screening_recorded',
          jsonb_build_object('screening_id', '50000000-0000-4000-8000-000000000013',
                             'child_id', '00000000-0000-4000-8000-00000000ca01',
                             'screened_at', now() + interval '10 days', 'model_version', '0.1.0',
                             'rules_version', '0.1.0', 'level', 'green'))))) -> 0 ->> 'code',
          'schema_invalid', 'timestamps far in the future are rejected');
select is((public.sync_events(jsonb_build_array(tests.ev('e0000000-0000-4000-8000-000000000014', 'screening_recorded',
          jsonb_build_object('screening_id', '50000000-0000-4000-8000-000000000014',
                             'child_id', '00000000-0000-4000-8000-00000000ca01',
                             'screened_at', now(), 'model_version', '0.1.0', 'rules_version', '0.1.0',
                             'level', 'purple'))))) -> 0 ->> 'code',
          'schema_invalid', 'invalid enum values are rejected');
select throws_ok($$ select public.sync_events((select jsonb_agg(tests.ev(gen_random_uuid(), 'child_upserted', '{}'))
                                               from generate_series(1, 101))) $$,
                 '22023', null, 'batches over the cap (100) are refused');
select throws_ok($$ select public.sync_events('[]'::jsonb) $$, '22023', null, 'empty batch is refused');
select throws_ok($$ select public.sync_events('{"event_id": "x"}'::jsonb) $$, '22023', null, 'non-array batch is refused');

-- Rejected results are stored and replay deterministically
select is((select count(*) from public.sync_event where result ->> 'status' = 'rejected')::int, 6,
          'terminal rejections are recorded in the ledger (not the payload)');
select ok(not exists (select 1 from public.sync_event e where e::text like '%Synthetic Name%'),
          'payload values are never stored in the ledger');

-- Ownership -------------------------------------------------------------------
select tests.as_user('00000000-0000-4000-8000-0000000000b1');
select is((public.sync_events(:'batch1'::jsonb)) -> 0 ->> 'code', 'event_id_conflict',
          'B replaying A''s event ids learns nothing (event_id_conflict)');
select is((public.sync_events(jsonb_build_array(tests.ev('e0000000-0000-4000-8000-0000000000b1', 'screening_recorded',
          jsonb_build_object('screening_id', '50000000-0000-4000-8000-0000000000b1',
                             'child_id', '00000000-0000-4000-8000-00000000ca01',
                             'screened_at', now(), 'model_version', '0.1.0', 'rules_version', '0.1.0',
                             'level', 'red'))))) -> 0 ->> 'code',
          'forbidden', 'B cannot sync data for A''s child');
select is((public.sync_events(jsonb_build_array(tests.ev('e0000000-0000-4000-8000-0000000000b2', 'consent_changed',
          jsonb_build_object('child_id', '00000000-0000-4000-8000-00000000ca01', 'consent_type', 'b',
                             'action', 'withdraw'))))) -> 0 ->> 'code',
          'forbidden', 'B cannot withdraw consent for A''s child');
select is((public.sync_events(jsonb_build_array(tests.ev('e0000000-0000-4000-8000-0000000000b3', 'consent_changed',
          jsonb_build_object('child_id', '00000000-0000-4000-8000-00000000cb02', 'consent_type', 'a',
                             'action', 'grant', 'text_version', 'v1', 'method', 'paper_via_kader'))))) -> 0 ->> 'code',
          'forbidden', 'only kader may record paper consent');

select tests.as_owner();
select is((select count(*) from public.screening where child_id = '00000000-0000-4000-8000-00000000ca01')::int, 1,
          'A''s child still has exactly one screening');
select ok(exists (select 1 from public.consent where child_id = '00000000-0000-4000-8000-00000000ca01'
                    and consent_type = 'b' and withdrawn_at is null), 'A''s consent (b) untouched');

select * from finish();
rollback;
