-- ADR 017 §2.4/2.5: sync_events for child accounts (allowed types, payload
-- allow-lists, limits, proof gates) and guardians (request_id auto-done,
-- consent d), request lifecycle (ack, expiry).
begin;
\ir _helpers.psql
select plan(44);

do $$ begin
  perform tests.make_child('00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000c3101', array['a', 'b', 'd']);
  perform tests.make_child('00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000c31b1');
  perform tests.new_child_user('00000000-0000-4000-8000-0000000d3101', '00000000-0000-4000-8000-0000000000a1',
                               '00000000-0000-4000-8000-0000000c3101', 'anak.tiga');
  insert into public.screening_request (id, child_id, requested_by, reason_code)
  values ('a1300000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000c31b1', null, 'other');
end $$;

-- ------------------------------------------------------------ child account
select tests.as_user('00000000-0000-4000-8000-0000000d3101');
select is((public.sync_events(jsonb_build_array(tests.ev('e1300000-0000-4000-8000-000000000001', 'brushing_recorded',
          jsonb_build_object('brushing_id', '61300000-0000-4000-8000-000000000001', 'child_id', '00000000-0000-4000-8000-0000000c3101',
                             'started_at', now() - interval '10 minutes', 'duration_s', 120, 'camera_mode', true))))) -> 0 ->> 'status',
          'applied', 'child account syncs brushing_recorded for its child');
select is((select recorded_by from public.brushing_event where id = '61300000-0000-4000-8000-000000000001'),
          '00000000-0000-4000-8000-0000000d3101'::uuid, '... recorded_by = the child account');

select jsonb_build_array(tests.ev('e1300000-0000-4000-8000-000000000002', 'screening_requested', jsonb_build_object(
  'request_id', 'a1300000-0000-4000-8000-000000000001', 'child_id', '00000000-0000-4000-8000-0000000c3101',
  'reason_code', 'toothache', 'pain_level', 7)))::text as req1 \gset
select public.sync_events(:'req1'::jsonb)::text as req1_r \gset
select is((:'req1_r'::jsonb) -> 0 ->> 'status', 'applied', 'screening_requested applied');
select results_eq($$ select status::text, requested_by, pain_level::int, reason_code from public.screening_request
                     where id = 'a1300000-0000-4000-8000-000000000001' $$,
                  $$ values ('pending'::text, '00000000-0000-4000-8000-0000000d3101'::uuid, 7, 'toothache'::text) $$,
                  'request stored as pending with the codes only');
select is(public.sync_events(:'req1'::jsonb), :'req1_r'::jsonb, 'replay is idempotent');
select is((public.sync_events(jsonb_build_array(tests.ev('e1300000-0000-4000-8000-000000000003', 'screening_requested',
          jsonb_build_object('request_id', 'a1300000-0000-4000-8000-0000000000f1', 'child_id', '00000000-0000-4000-8000-0000000c31b1',
                             'reason_code', 'other'))))) -> 0 ->> 'code', 'forbidden', 'request for another child -> forbidden');
select is((public.sync_events(jsonb_build_array(tests.ev('e1300000-0000-4000-8000-000000000004', 'screening_requested',
          jsonb_build_object('request_id', 'a1300000-0000-4000-8000-0000000000f2', 'child_id', '00000000-0000-4000-8000-0000000c3101',
                             'reason_code', 'gigi sakit sekali'))))) -> 0 ->> 'code', 'schema_invalid', 'free-text reason refused');
select is((public.sync_events(jsonb_build_array(tests.ev('e1300000-0000-4000-8000-000000000005', 'screening_requested',
          jsonb_build_object('request_id', 'a1300000-0000-4000-8000-0000000000f3', 'child_id', '00000000-0000-4000-8000-0000000c3101',
                             'reason_code', 'other', 'pain_level', 11))))) -> 0 ->> 'code', 'schema_invalid', 'pain_level above 10 refused');
select is((public.sync_events(jsonb_build_array(tests.ev('e1300000-0000-4000-8000-000000000006', 'screening_requested',
          jsonb_build_object('request_id', 'a1300000-0000-4000-8000-0000000000f4', 'child_id', '00000000-0000-4000-8000-0000000c3101',
                             'reason_code', 'other', 'note', 'Budi sakit'))))) -> 0 ->> 'code', 'schema_invalid', 'unknown payload keys refused');
select is((public.sync_events(jsonb_build_array(tests.ev('e1300000-0000-4000-8000-000000000007', 'screening_requested',
          jsonb_build_object('request_id', 'a1300000-0000-4000-8000-0000000000f5', 'child_id', '00000000-0000-4000-8000-0000000c3101',
                             'reason_code', 'other', 'pain_level', '7'))))) -> 0 ->> 'code', 'schema_invalid', 'pain_level must be a number');
select is((select array_agg(e ->> 'code') from jsonb_array_elements(public.sync_events(jsonb_build_array(
            tests.ev('e1300000-0000-4000-8000-000000000008', 'screening_recorded', jsonb_build_object(
              'screening_id', '51300000-0000-4000-8000-0000000000f1', 'child_id', '00000000-0000-4000-8000-0000000c3101',
              'screened_at', now(), 'model_version', '0.1.0', 'rules_version', '0.1.0', 'level', 'green')),
            tests.ev('e1300000-0000-4000-8000-000000000009', 'consent_changed', jsonb_build_object(
              'child_id', '00000000-0000-4000-8000-0000000c3101', 'consent_type', 'c', 'action', 'grant', 'text_version', 'v1')),
            tests.ev('e1300000-0000-4000-8000-000000000010', 'child_upserted', jsonb_build_object(
              'child_id', '00000000-0000-4000-8000-0000000c3101', 'birth_year', 2016)),
            tests.ev('e1300000-0000-4000-8000-000000000011', 'referral_created', jsonb_build_object(
              'referral_id', '71300000-0000-4000-8000-0000000000f1', 'child_id', '00000000-0000-4000-8000-0000000c3101',
              'level', 'red'))))) e),
          array['forbidden', 'forbidden', 'forbidden', 'forbidden'],
          'child account: screening/consent/profile/referral events are forbidden');
select is((select array_agg(coalesce(e ->> 'code', e ->> 'status')) from jsonb_array_elements(public.sync_events(
            (select jsonb_agg(tests.ev(('e1300000-0000-4000-8000-0000000001' || lpad(g::text, 2, '0'))::uuid, 'screening_requested',
                      jsonb_build_object('request_id', ('a1300000-0000-4000-8000-0000000000' || lpad(g::text, 2, '0'))::text,
                                         'child_id', '00000000-0000-4000-8000-0000000c3101', 'reason_code', 'sensitive_teeth'))
                    order by g)
             from generate_series(2, 6) g))) e),
          array['applied', 'applied', 'applied', 'applied', 'limit_exceeded'],
          'request_max_per_child_per_day (5) -> limit_exceeded');

-- proofs
select is((public.sync_events(jsonb_build_array(tests.ev('e1300000-0000-4000-8000-000000000020', 'brushing_proof_registered',
          jsonb_build_object('proof_id', 'b1300000-0000-4000-8000-000000000001', 'child_id', '00000000-0000-4000-8000-0000000c3101',
                             'brushing_event_id', '61300000-0000-4000-8000-000000000001', 'size_bytes', 4000000,
                             'duration_s', 90, 'mime', 'video/mp4'))))) -> 0 ->> 'status',
          'applied', 'brushing_proof_registered applied (flag on, consent b + d)');
select ok((select object_path = '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-0000000c3101/b1300000-0000-4000-8000-000000000001.mp4'
                  and status = 'uploaded' and expires_at between now() + interval '6 days' and now() + interval '8 days'
           from public.brushing_proof where id = 'b1300000-0000-4000-8000-000000000001'),
          'server builds <guardian>/<child>/<proof>.mp4 and expires_at = now + proof_retention_days');
select is((public.sync_events(jsonb_build_array(tests.ev('e1300000-0000-4000-8000-000000000021', 'brushing_proof_registered',
          jsonb_build_object('proof_id', 'b1300000-0000-4000-8000-000000000002', 'child_id', '00000000-0000-4000-8000-0000000c3101',
                             'brushing_event_id', '61300000-0000-4000-8000-000000000001', 'size_bytes', 4000000,
                             'duration_s', 90, 'mime', 'video/mp4'))))) -> 0 ->> 'code',
          'entity_conflict', 'one proof per brushing event');
select is((public.sync_events(jsonb_build_array(tests.ev('e1300000-0000-4000-8000-000000000022', 'brushing_proof_registered',
          jsonb_build_object('proof_id', 'b1300000-0000-4000-8000-000000000003', 'child_id', '00000000-0000-4000-8000-0000000c3101',
                             'brushing_event_id', '61300000-0000-4000-8000-0000000000ff', 'size_bytes', 4000000,
                             'duration_s', 90, 'mime', 'video/mp4'))))) -> 0 ->> 'code',
          'schema_invalid', 'proof needs an applied brushing event of the child');
select is((public.sync_events(jsonb_build_array(tests.ev('e1300000-0000-4000-8000-000000000023', 'brushing_proof_registered',
          jsonb_build_object('proof_id', 'b1300000-0000-4000-8000-000000000004', 'child_id', '00000000-0000-4000-8000-0000000c3101',
                             'brushing_event_id', '61300000-0000-4000-8000-000000000001', 'size_bytes', 20000000,
                             'duration_s', 90, 'mime', 'video/mp4'))))) -> 0 ->> 'code',
          'limit_exceeded', 'proof above proof_max_bytes -> limit_exceeded');
select is((public.sync_events(jsonb_build_array(tests.ev('e1300000-0000-4000-8000-000000000024', 'brushing_proof_registered',
          jsonb_build_object('proof_id', 'b1300000-0000-4000-8000-000000000005', 'child_id', '00000000-0000-4000-8000-0000000c3101',
                             'brushing_event_id', '61300000-0000-4000-8000-000000000001', 'size_bytes', 1000,
                             'duration_s', 200, 'mime', 'video/mp4'))))) -> 0 ->> 'code',
          'limit_exceeded', 'proof above proof_max_duration_s -> limit_exceeded');
select is((public.sync_events(jsonb_build_array(tests.ev('e1300000-0000-4000-8000-000000000025', 'brushing_proof_registered',
          jsonb_build_object('proof_id', 'b1300000-0000-4000-8000-000000000006', 'child_id', '00000000-0000-4000-8000-0000000c3101',
                             'brushing_event_id', '61300000-0000-4000-8000-000000000001', 'size_bytes', 1000,
                             'duration_s', 20, 'mime', 'video/webm'))))) -> 0 ->> 'code',
          'schema_invalid', 'only video/mp4 and video/quicktime');
select is((select array_agg(coalesce(e ->> 'code', e ->> 'status')) from jsonb_array_elements(public.sync_events(jsonb_build_array(
            tests.ev('e1300000-0000-4000-8000-000000000030', 'brushing_recorded', jsonb_build_object(
              'brushing_id', '61300000-0000-4000-8000-000000000002', 'child_id', '00000000-0000-4000-8000-0000000c3101',
              'started_at', now() - interval '9 minutes', 'duration_s', 100)),
            tests.ev('e1300000-0000-4000-8000-000000000031', 'brushing_recorded', jsonb_build_object(
              'brushing_id', '61300000-0000-4000-8000-000000000003', 'child_id', '00000000-0000-4000-8000-0000000c3101',
              'started_at', now() - interval '8 minutes', 'duration_s', 100)),
            tests.ev('e1300000-0000-4000-8000-000000000032', 'brushing_recorded', jsonb_build_object(
              'brushing_id', '61300000-0000-4000-8000-000000000004', 'child_id', '00000000-0000-4000-8000-0000000c3101',
              'started_at', now() - interval '7 minutes', 'duration_s', 100)),
            tests.ev('e1300000-0000-4000-8000-000000000033', 'brushing_proof_registered', jsonb_build_object(
              'proof_id', 'b1300000-0000-4000-8000-000000000012', 'child_id', '00000000-0000-4000-8000-0000000c3101',
              'brushing_event_id', '61300000-0000-4000-8000-000000000002', 'size_bytes', 1000, 'duration_s', 20, 'mime', 'video/quicktime')),
            tests.ev('e1300000-0000-4000-8000-000000000034', 'brushing_proof_registered', jsonb_build_object(
              'proof_id', 'b1300000-0000-4000-8000-000000000013', 'child_id', '00000000-0000-4000-8000-0000000c3101',
              'brushing_event_id', '61300000-0000-4000-8000-000000000003', 'size_bytes', 1000, 'duration_s', 20, 'mime', 'video/mp4')),
            tests.ev('e1300000-0000-4000-8000-000000000035', 'brushing_proof_registered', jsonb_build_object(
              'proof_id', 'b1300000-0000-4000-8000-000000000014', 'child_id', '00000000-0000-4000-8000-0000000c3101',
              'brushing_event_id', '61300000-0000-4000-8000-000000000004', 'size_bytes', 1000, 'duration_s', 20, 'mime', 'video/mp4'))
          ))) e),
          array['applied', 'applied', 'applied', 'applied', 'applied', 'limit_exceeded'],
          'proof_max_per_child_per_day (3) -> limit_exceeded');

select tests.as_owner();
update public.feature_flag set enabled = false where key = 'brushing_proof_video';
select tests.as_user('00000000-0000-4000-8000-0000000d3101');
select is((public.sync_events(jsonb_build_array(tests.ev('e1300000-0000-4000-8000-000000000040', 'brushing_proof_registered',
          jsonb_build_object('proof_id', 'b1300000-0000-4000-8000-000000000020', 'child_id', '00000000-0000-4000-8000-0000000c3101',
                             'brushing_event_id', '61300000-0000-4000-8000-000000000004', 'size_bytes', 1000,
                             'duration_s', 20, 'mime', 'video/mp4'))))) -> 0 ->> 'code',
          'feature_disabled', 'flag brushing_proof_video OFF -> feature_disabled');
select tests.as_owner();
update public.feature_flag set enabled = true where key = 'brushing_proof_video';
update public.consent set withdrawn_at = now()
 where child_id = '00000000-0000-4000-8000-0000000c3101' and consent_type = 'd' and withdrawn_at is null;
select tests.as_user('00000000-0000-4000-8000-0000000d3101');
select is((public.sync_events(jsonb_build_array(tests.ev('e1300000-0000-4000-8000-000000000041', 'brushing_proof_registered',
          jsonb_build_object('proof_id', 'b1300000-0000-4000-8000-000000000021', 'child_id', '00000000-0000-4000-8000-0000000c3101',
                             'brushing_event_id', '61300000-0000-4000-8000-000000000004', 'size_bytes', 1000,
                             'duration_s', 20, 'mime', 'video/mp4'))))) -> 0 ->> 'code',
          'consent_required', 'consent (d) withdrawn -> consent_required');

-- ------------------------------------------------------------ guardian A
select tests.as_user('00000000-0000-4000-8000-0000000000a1');
select is((public.sync_events(jsonb_build_array(tests.ev('e1300000-0000-4000-8000-000000000050', 'screening_requested',
          jsonb_build_object('request_id', 'a1300000-0000-4000-8000-0000000000f9', 'child_id', '00000000-0000-4000-8000-0000000c3101',
                             'reason_code', 'other'))))) -> 0 ->> 'code', 'forbidden', 'guardians do not send screening_requested');
select is((public.sync_events(jsonb_build_array(tests.ev('e1300000-0000-4000-8000-000000000051', 'brushing_proof_registered',
          jsonb_build_object('proof_id', 'b1300000-0000-4000-8000-0000000000f9', 'child_id', '00000000-0000-4000-8000-0000000c3101',
                             'brushing_event_id', '61300000-0000-4000-8000-000000000004', 'size_bytes', 1000,
                             'duration_s', 20, 'mime', 'video/mp4'))))) -> 0 ->> 'code', 'forbidden', 'guardians do not register proofs');
select is((public.sync_events(jsonb_build_array(tests.ev('e1300000-0000-4000-8000-000000000052', 'screening_recorded',
          jsonb_build_object('screening_id', '51300000-0000-4000-8000-000000000001', 'child_id', '00000000-0000-4000-8000-0000000c3101',
                             'screened_at', now() - interval '1 minute', 'model_version', '0.1.0', 'rules_version', '0.1.0',
                             'level', 'yellow', 'request_id', 'a1300000-0000-4000-8000-000000000001'))))) -> 0 ->> 'status',
          'applied', 'screening_recorded with request_id applied');
select ok((select status = 'done' and responded_at is not null and screening_id = '51300000-0000-4000-8000-000000000001'
           from public.screening_request where id = 'a1300000-0000-4000-8000-000000000001'),
          'the answered request is done (responded_at, screening_id)');
select is((public.sync_events(jsonb_build_array(tests.ev('e1300000-0000-4000-8000-000000000053', 'screening_recorded',
          jsonb_build_object('screening_id', '51300000-0000-4000-8000-000000000002', 'child_id', '00000000-0000-4000-8000-0000000c3101',
                             'screened_at', now(), 'model_version', '0.1.0', 'rules_version', '0.1.0',
                             'level', 'green', 'request_id', 'a1300000-0000-4000-8000-0000000000b1'))))) -> 0 ->> 'code',
          'forbidden', 'request_id of another child''s request -> forbidden');
select is((public.sync_events(jsonb_build_array(tests.ev('e1300000-0000-4000-8000-000000000054', 'screening_recorded',
          jsonb_build_object('screening_id', '51300000-0000-4000-8000-000000000003', 'child_id', '00000000-0000-4000-8000-0000000c3101',
                             'screened_at', now(), 'model_version', '0.1.0', 'rules_version', '0.1.0',
                             'level', 'green', 'request_id', 'nope'))))) -> 0 ->> 'code',
          'schema_invalid', 'request_id must be a uuid');
select is((public.sync_events(jsonb_build_array(tests.ev('e1300000-0000-4000-8000-000000000055', 'screening_recorded',
          jsonb_build_object('screening_id', '51300000-0000-4000-8000-000000000004', 'child_id', '00000000-0000-4000-8000-0000000c3101',
                             'screened_at', now(), 'model_version', '0.1.0', 'rules_version', '0.1.0',
                             'level', 'green', 'request_id', 'a1300000-0000-4000-8000-000000000001'))))) -> 0 ->> 'status',
          'applied', 'a second screening may cite a done request ...');
select is((select screening_id from public.screening_request where id = 'a1300000-0000-4000-8000-000000000001'),
          '51300000-0000-4000-8000-000000000001'::uuid, '... and the request keeps its first answer');

-- ack lifecycle
select is(public.ack_screening_request('a1300000-0000-4000-8000-000000000002', 'seen') ->> 'status', 'seen',
          'ack seen: pending -> seen');
select is(public.ack_screening_request('a1300000-0000-4000-8000-000000000002', 'seen') ->> 'seen_at',
          (select to_jsonb(seen_at) #>> '{}' from public.screening_request where id = 'a1300000-0000-4000-8000-000000000002'),
          'ack seen is idempotent');
select ok((select x ->> 'status' = 'cancelled' and x ->> 'responded_at' is not null
           from (select public.ack_screening_request('a1300000-0000-4000-8000-000000000002', 'cancel') as x) t),
          'ack cancel: seen -> cancelled with responded_at');
select is(public.ack_screening_request('a1300000-0000-4000-8000-000000000001', 'cancel') ->> 'status', 'done',
          'a done request cannot be cancelled');
select throws_ok($$ select public.ack_screening_request('a1300000-0000-4000-8000-000000000003', 'delete') $$,
                 '22023', null, 'unknown ack action refused');
select tests.as_owner();
update public.screening_request set created_at = now() - interval '20 days', expires_at = now() - interval '6 days'
 where id = 'a1300000-0000-4000-8000-000000000003';
select tests.as_user('00000000-0000-4000-8000-0000000000a1');
select is((select status::text from public.list_screening_requests('00000000-0000-4000-8000-0000000c3101')
           where id = 'a1300000-0000-4000-8000-000000000003'), 'expired',
          'list shows the effective status expired for an overdue request');
select is(public.ack_screening_request('a1300000-0000-4000-8000-000000000003', 'seen') ->> 'status', 'expired',
          'ack on an overdue request answers expired ...');
select is((select status::text from public.screening_request where id = 'a1300000-0000-4000-8000-000000000003'), 'expired',
          '... and stores it');
select is((select count(*) from public.list_screening_requests(null, array['pending']::public.request_status[]))::int, 2,
          'status filter: two requests still pending');

select tests.as_user('00000000-0000-4000-8000-0000000d3101');
select is((select count(*) from public.list_screening_requests())::int, 5, 'child account lists its own requests');
select throws_ok($$ select * from public.list_screening_requests('00000000-0000-4000-8000-0000000c31b1') $$,
                 '42501', null, 'child account cannot list another child''s requests');

select tests.as_owner();
update public.screening_request set created_at = now() - interval '20 days', expires_at = now() - interval '1 day'
 where id = 'a1300000-0000-4000-8000-000000000004';
select tests.as_service();
select is((public.service_retention_sweep_db() ->> 'screening_request_expired')::int, 1,
          'daily retention marks overdue requests expired');

select tests.as_user('00000000-0000-4000-8000-0000000000b1');
select is((public.sync_events(jsonb_build_array(tests.ev('e1300000-0000-4000-8000-000000000060', 'consent_changed',
          jsonb_build_object('child_id', '00000000-0000-4000-8000-0000000c31b1', 'consent_type', 'd', 'action', 'grant',
                             'text_version', 'consent-d-v1'))))) -> 0 ->> 'status', 'applied', 'consent_changed accepts type d');

select tests.as_user('00000000-0000-4000-8000-0000000000a1');
select public.request_deletion('account');
select tests.as_user('00000000-0000-4000-8000-0000000d3101');
select is((public.sync_events(jsonb_build_array(tests.ev('e1300000-0000-4000-8000-000000000070', 'brushing_recorded',
          jsonb_build_object('brushing_id', '61300000-0000-4000-8000-000000000070', 'child_id', '00000000-0000-4000-8000-0000000c3101',
                             'started_at', now() - interval '1 minute', 'duration_s', 100))))) -> 0 ->> 'code',
          'deletion_pending', 'a pending deletion of the guardian account stops the child account''s sync');

select tests.as_owner();
select * from finish();
rollback;
