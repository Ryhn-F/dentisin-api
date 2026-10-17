-- ADR 017 §2.3: RLS matrix guardian vs child account vs other guardian vs
-- disabled child account on every new/changed table.
begin;
\ir _helpers.psql
select plan(49);

-- Fixtures (owner) ------------------------------------------------------------
--   ca21 (A, consents a b d) + child account CA  d2101 'anak.a'
--   ca22 (A, consents a b)   + child account CD  d2102 'anak.c' (disabled)
--   cb21 (B, consents a b d) + child account CB  d21b1 'anak.b'
do $$ begin
  perform tests.make_child('00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000c2101', array['a', 'b', 'd']);
  perform tests.make_child('00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000c2102');
  perform tests.make_child('00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000c21b1', array['a', 'b', 'd']);
  perform tests.new_child_user('00000000-0000-4000-8000-0000000d2101', '00000000-0000-4000-8000-0000000000a1',
                               '00000000-0000-4000-8000-0000000c2101', 'anak.a');
  perform tests.new_child_user('00000000-0000-4000-8000-0000000d2102', '00000000-0000-4000-8000-0000000000a1',
                               '00000000-0000-4000-8000-0000000c2102', 'anak.c');
  perform tests.new_child_user('00000000-0000-4000-8000-0000000d21b1', '00000000-0000-4000-8000-0000000000b1',
                               '00000000-0000-4000-8000-0000000c21b1', 'anak.b');
  update public.child_account set disabled_at = now() where user_id = '00000000-0000-4000-8000-0000000d2102';

  insert into public.screening (id, child_id, recorded_by, screened_at, model_version, rules_version, level)
  values ('51200000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-0000000c2101',
          '00000000-0000-4000-8000-0000000000a1', now() - interval '2 hours', '1.0.0', '1.0.0', 'yellow');
  insert into public.referral (id, child_id, created_by, level, referred_at)
  values ('71200000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-0000000c2101',
          '00000000-0000-4000-8000-0000000000a1', 'yellow', now() - interval '2 hours');
  insert into public.brushing_event (id, child_id, recorded_by, started_at, duration_s) values
    ('61200000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-0000000c2101',
     '00000000-0000-4000-8000-0000000000a1', now() - interval '1 hour', 120),
    ('61200000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000c21b1',
     '00000000-0000-4000-8000-0000000000b1', now() - interval '1 hour', 120),
    ('61200000-0000-4000-8000-000000000002', '00000000-0000-4000-8000-0000000c2102',
     '00000000-0000-4000-8000-0000000000a1', now() - interval '1 hour', 120);
  insert into public.screening_request (id, child_id, requested_by, reason_code, pain_level) values
    ('a1200000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-0000000c2101',
     '00000000-0000-4000-8000-0000000d2101', 'toothache', 6),
    ('a1200000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000c21b1',
     '00000000-0000-4000-8000-0000000d21b1', 'other', null);
  insert into public.brushing_proof (id, child_id, brushing_event_id, uploaded_by, object_path, mime, size_bytes, duration_s)
  values ('b1200000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-0000000c2101',
          '61200000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-0000000d2101',
          '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-0000000c2101/b1200000-0000-4000-8000-000000000001.mp4',
          'video/mp4', 1000, 30);
end $$;

-- ------------------------------------------------------------ child account CA
select tests.as_user('00000000-0000-4000-8000-0000000d2101');
select is((select array_agg(id) from public.child_profile), array['00000000-0000-4000-8000-0000000c2101'::uuid],
          'child account reads exactly its own child_profile row');
select is((select count(*) from public.consent)::int + (select count(*) from public.screening)::int
          + (select count(*) from public.referral)::int + (select count(*) from public.image_upload)::int, 0,
          'child account: no consent / screening / referral / image_upload rows');
select is((select count(*) from public.brushing_event)::int, 1, 'child account reads only its child''s brushing events');
select is((select count(*) from public.screening_request)::int, 1, 'child account reads only its child''s requests');
select is((select count(*) from public.brushing_proof)::int, 1, 'child account reads its child''s live proof metadata');
select is((select array_agg(user_id) from public.child_account), array['00000000-0000-4000-8000-0000000d2101'::uuid],
          'child account reads only its own child_account row');
select is((select count(*) from public.guardian)::int, 0, 'child account has no guardian row');
select is((select array_agg(role::text) from public.user_role), array['child'], 'child account sees its child role');
select lives_ok($$ insert into public.brushing_event (id, child_id, recorded_by, started_at, duration_s)
                   values ('61200000-0000-4000-8000-00000000021c', '00000000-0000-4000-8000-0000000c2101',
                           '00000000-0000-4000-8000-0000000d2101', now() - interval '5 minutes', 95) $$,
                'child account records brushing for its own child');
select throws_ok($$ insert into public.brushing_event (id, child_id, recorded_by, started_at, duration_s)
                    values ('61200000-0000-4000-8000-0000000002ff', '00000000-0000-4000-8000-0000000c21b1',
                            '00000000-0000-4000-8000-0000000d2101', now(), 95) $$,
                 '42501', null, 'child account cannot record for another child');
select throws_ok($$ insert into public.brushing_event (id, child_id, recorded_by, started_at, duration_s)
                    values ('61200000-0000-4000-8000-0000000002fe', '00000000-0000-4000-8000-0000000c2101',
                            '00000000-0000-4000-8000-0000000000a1', now(), 95) $$,
                 '42501', null, 'child account cannot spoof recorded_by');
select throws_ok($$ insert into public.screening (id, child_id, recorded_by, screened_at, model_version, rules_version, level)
                    values ('51200000-0000-4000-8000-0000000000ff', '00000000-0000-4000-8000-0000000c2101',
                            '00000000-0000-4000-8000-0000000d2101', now(), '1.0.0', '1.0.0', 'green') $$,
                 '42501', null, 'child account cannot record a screening');
select lives_ok($$ insert into public.screening_request (id, child_id, requested_by, reason_code)
                   values ('a1200000-0000-4000-8000-000000000002', '00000000-0000-4000-8000-0000000c2101',
                           '00000000-0000-4000-8000-0000000d2101', 'bleeding_gums') $$,
                'child account creates a screening request');
select ok((select status = 'pending' and expires_at between now() + interval '13 days' and now() + interval '15 days'
           from public.screening_request where id = 'a1200000-0000-4000-8000-000000000002'),
          'request: server sets status pending and expires_at = now + request_expiry_days');
select throws_ok($$ insert into public.screening_request (id, child_id, requested_by, reason_code)
                    values ('a1200000-0000-4000-8000-0000000000ff', '00000000-0000-4000-8000-0000000c21b1',
                            '00000000-0000-4000-8000-0000000d2101', 'other') $$,
                 '42501', null, 'child account cannot request for another child');
select throws_ok($$ insert into public.consent (child_id, granted_by, consent_type, text_version, granted_at)
                    values ('00000000-0000-4000-8000-0000000c2101', '00000000-0000-4000-8000-0000000d2101', 'c', 'v1', now()) $$,
                 '42501', null, 'child account cannot grant consent');
select throws_ok($$ update public.screening_request set status = 'done' $$, '42501', null,
                 'child account cannot change request status');
select throws_ok($$ delete from public.brushing_event $$, '42501', null, 'child account cannot delete brushing events');
select throws_ok($$ insert into public.child_account (user_id, child_id, guardian_id, username)
                    values ('00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000c21b1',
                            '00000000-0000-4000-8000-0000000000b1', 'palsu') $$,
                 '42501', null, 'clients cannot create child accounts');
select throws_ok($$ update public.child_account set username = 'ganti' $$, '42501', null,
                 'clients cannot modify child_account');
select throws_ok($$ insert into public.brushing_proof (id, child_id, brushing_event_id, uploaded_by, object_path, mime, size_bytes, duration_s)
                    values ('b1200000-0000-4000-8000-0000000000fe', '00000000-0000-4000-8000-0000000c2101',
                            '61200000-0000-4000-8000-00000000021c', '00000000-0000-4000-8000-0000000d2101',
                            '00000000-0000-4000-8000-0000000d2101/00000000-0000-4000-8000-0000000c2101/b1200000-0000-4000-8000-0000000000fe.mp4',
                            'video/mp4', 1000, 30) $$,
                 '23514', null, 'proof object_path must be exactly <guardian>/<child>/<proof>.<ext>');
select throws_ok($$ select * from public.list_children() $$, '42501', null, 'child account cannot list children');
select throws_ok($$ select public.child_history('00000000-0000-4000-8000-0000000c21b1') $$, '42501', null,
                 'child account cannot read another child''s history');
select is((public.sync_events(jsonb_build_array(tests.ev('e1200000-0000-4000-8000-000000000001', 'consent_changed',
          jsonb_build_object('child_id', '00000000-0000-4000-8000-0000000c2101', 'consent_type', 'd',
                             'action', 'withdraw'))))) -> 0 ->> 'code',
          'forbidden', 'child account cannot send consent events');

-- ------------------------------------------------------------ other guardian B
select tests.as_user('00000000-0000-4000-8000-0000000000b1');
select is((select count(*) from public.screening_request where child_id = '00000000-0000-4000-8000-0000000c2101')::int
          + (select count(*) from public.brushing_proof where child_id = '00000000-0000-4000-8000-0000000c2101')::int
          + (select count(*) from public.child_account where guardian_id = '00000000-0000-4000-8000-0000000000a1')::int, 0,
          'B sees none of A''s requests, proofs or child accounts');
select throws_ok($$ select public.ack_screening_request('a1200000-0000-4000-8000-000000000001', 'seen') $$,
                 '42501', null, 'B cannot acknowledge A''s request');
select throws_ok($$ select public.ack_screening_request('a1200000-0000-4000-8000-0000000000aa', 'seen') $$,
                 '42501', null, 'unknown request id gives the same error (no enumeration)');
select throws_ok($$ select public.mark_proof_viewed('b1200000-0000-4000-8000-000000000001') $$,
                 '42501', null, 'B cannot mark A''s proof viewed');
select throws_ok($$ select public.delete_brushing_proof('b1200000-0000-4000-8000-000000000001') $$,
                 '42501', null, 'B cannot delete A''s proof');
select throws_ok($$ select public.child_history('00000000-0000-4000-8000-0000000c2101') $$,
                 '42501', null, 'B cannot read A''s child history');
select throws_ok($$ select public.brushing_streak('00000000-0000-4000-8000-0000000c2101') $$,
                 '42501', null, 'B cannot read A''s child streak');
select throws_ok($$ select * from public.list_screening_requests('00000000-0000-4000-8000-0000000c2101') $$,
                 '42501', null, 'B cannot list A''s child requests');
select is((select array_agg(id) from public.list_screening_requests()), array['a1200000-0000-4000-8000-0000000000b1'::uuid],
          'B lists only requests of own children');

-- ------------------------------------------------------------ guardian A
select tests.as_user('00000000-0000-4000-8000-0000000000a1');
select is((select count(*) from public.child_account)::int, 2, 'A reads the accounts of own children (incl. disabled)');
select is((select count(*) from public.screening_request)::int, 2, 'A reads own children''s requests');
select is((select count(*) from public.brushing_proof)::int, 1, 'A reads own children''s live proofs');
select throws_ok($$ insert into public.screening_request (id, child_id, requested_by, reason_code)
                    values ('a1200000-0000-4000-8000-0000000000fd', '00000000-0000-4000-8000-0000000c2101',
                            '00000000-0000-4000-8000-0000000000a1', 'other') $$,
                 '42501', null, 'guardians do not create screening requests');
select throws_ok($$ insert into public.brushing_proof (id, child_id, brushing_event_id, uploaded_by, object_path, mime, size_bytes, duration_s)
                    values ('b1200000-0000-4000-8000-0000000000fd', '00000000-0000-4000-8000-0000000c2101',
                            '61200000-0000-4000-8000-00000000021c', '00000000-0000-4000-8000-0000000000a1',
                            '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-0000000c2101/b1200000-0000-4000-8000-0000000000fd.mp4',
                            'video/mp4', 1000, 30) $$,
                 '42501', null, 'guardians do not register proofs');
select throws_ok($$ update public.child_account set disabled_at = null $$, '42501', null,
                 'guardians change child accounts only through the Edge Function');
select results_eq($$ select child_id, has_child_account, child_account_disabled, username, open_requests, unviewed_proofs
                     from public.list_children() order by child_id $$,
                  $$ values ('00000000-0000-4000-8000-0000000c2101'::uuid, true, false, 'anak.a'::text, 2, 1),
                            ('00000000-0000-4000-8000-0000000c2102'::uuid, true, true, 'anak.c'::text, 0, 0) $$,
                  'list_children: account state, open requests, unviewed proofs');

-- ------------------------------------------------------------ disabled child account
select tests.as_user('00000000-0000-4000-8000-0000000d2102');
select is((select count(*) from public.child_profile)::int + (select count(*) from public.brushing_event)::int
          + (select count(*) from public.child_account)::int + (select count(*) from public.screening_request)::int
          + (select count(*) from public.brushing_proof)::int, 0,
          'disabled child account reads nothing');
select throws_ok($$ insert into public.brushing_event (id, child_id, recorded_by, started_at, duration_s)
                    values ('61200000-0000-4000-8000-0000000002fd', '00000000-0000-4000-8000-0000000c2102',
                            '00000000-0000-4000-8000-0000000d2102', now(), 95) $$,
                 '42501', null, 'disabled child account cannot write');
select is((public.sync_events(jsonb_build_array(tests.ev('e1200000-0000-4000-8000-000000000002', 'brushing_recorded',
          jsonb_build_object('brushing_id', '61200000-0000-4000-8000-0000000002fc',
                             'child_id', '00000000-0000-4000-8000-0000000c2102',
                             'started_at', now() - interval '1 minute', 'duration_s', 100))))) -> 0 ->> 'code',
          'forbidden', 'disabled child account cannot sync');
select is((public.my_account() -> 'child_account' ->> 'disabled')::boolean, true,
          'my_account tells a disabled child account that it is disabled');
select throws_ok($$ insert into public.child_profile (id, owner_id) values ('00000000-0000-4000-8000-0000000c21ff',
                    '00000000-0000-4000-8000-0000000d2102') $$,
                 '42501', null, 'a child account can never own a child profile');

-- ------------------------------------------------------------ anon
select tests.as_anon();
select throws_ok($$ select * from public.child_account $$, '42501', null, 'anon: child_account denied');
select throws_ok($$ select * from public.screening_request $$, '42501', null, 'anon: screening_request denied');
select throws_ok($$ select * from public.brushing_proof $$, '42501', null, 'anon: brushing_proof denied');
select throws_ok($$ select * from public.child_login_attempt $$, '42501', null, 'anon: child_login_attempt denied');

select tests.as_owner();
select * from finish();
rollback;
