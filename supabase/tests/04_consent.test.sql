-- Consent: granting via sync creates the child shell; withdrawing (b) stops
-- sync (RPC + RLS backstop); withdrawal of (c) marks photos withdrawn.
begin;
\ir _helpers.psql
select plan(17);

select tests.as_user('00000000-0000-4000-8000-0000000000a1');

-- New child: data before consent (b) is refused
select is((public.sync_events(jsonb_build_array(tests.ev('e4000000-0000-4000-8000-000000000001', 'child_upserted',
          jsonb_build_object('child_id', '00000000-0000-4000-8000-00000000ca04', 'birth_year', 2017))))) -> 0 ->> 'code',
          'forbidden', 'child data for an unknown child (no consent yet) is refused');

select is((public.sync_events(jsonb_build_array(
            tests.ev('e4000000-0000-4000-8000-000000000002', 'consent_changed', jsonb_build_object(
              'child_id', '00000000-0000-4000-8000-00000000ca04', 'consent_type', 'a', 'action', 'grant',
              'text_version', 'consent-a-v1', 'at', now() - interval '5 minutes')),
            tests.ev('e4000000-0000-4000-8000-000000000003', 'child_upserted', jsonb_build_object(
              'child_id', '00000000-0000-4000-8000-00000000ca04', 'birth_year', 2017, 'birth_month', 2)),
            tests.ev('e4000000-0000-4000-8000-000000000004', 'consent_changed', jsonb_build_object(
              'child_id', '00000000-0000-4000-8000-00000000ca04', 'consent_type', 'b', 'action', 'grant',
              'text_version', 'consent-b-v1', 'at', now() - interval '5 minutes')),
            tests.ev('e4000000-0000-4000-8000-000000000005', 'child_upserted', jsonb_build_object(
              'child_id', '00000000-0000-4000-8000-00000000ca04', 'birth_year', 2017, 'birth_month', 2))
          ))) -> 1 ->> 'code',
          'consent_required', 'with only consent (a) the child shell exists but data sync is refused');

select results_eq($$ select birth_year::int, birth_month::int from public.child_profile
                     where id = '00000000-0000-4000-8000-00000000ca04' $$,
                  $$ values (2017, 2) $$, 'after consent (b) the profile data is stored');

select is((public.sync_events(jsonb_build_array(tests.ev('e4000000-0000-4000-8000-000000000006', 'screening_recorded',
          jsonb_build_object('screening_id', '54000000-0000-4000-8000-000000000001',
                             'child_id', '00000000-0000-4000-8000-00000000ca04', 'screened_at', now(),
                             'model_version', '0.1.0', 'rules_version', '0.1.0', 'level', 'green'))))) -> 0 ->> 'status',
          'applied', 'screening syncs while consent (b) is active');

-- Withdraw (b)
select is((public.sync_events(jsonb_build_array(tests.ev('e4000000-0000-4000-8000-000000000007', 'consent_changed',
          jsonb_build_object('child_id', '00000000-0000-4000-8000-00000000ca04', 'consent_type', 'b',
                             'action', 'withdraw'))))) -> 0 ->> 'status',
          'applied', 'withdrawal of (b) accepted');
select ok(exists (select 1 from public.consent where child_id = '00000000-0000-4000-8000-00000000ca04'
                    and consent_type = 'b' and withdrawn_at is not null), 'withdrawn_at recorded');

select is((public.sync_events(jsonb_build_array(
            tests.ev('e4000000-0000-4000-8000-000000000008', 'screening_recorded', jsonb_build_object(
              'screening_id', '54000000-0000-4000-8000-000000000002', 'child_id', '00000000-0000-4000-8000-00000000ca04',
              'screened_at', now(), 'model_version', '0.1.0', 'rules_version', '0.1.0', 'level', 'green')),
            tests.ev('e4000000-0000-4000-8000-000000000009', 'brushing_recorded', jsonb_build_object(
              'brushing_id', '64000000-0000-4000-8000-000000000001', 'child_id', '00000000-0000-4000-8000-00000000ca04',
              'started_at', now(), 'duration_s', 100)),
            tests.ev('e4000000-0000-4000-8000-000000000010', 'referral_created', jsonb_build_object(
              'referral_id', '74000000-0000-4000-8000-000000000001', 'child_id', '00000000-0000-4000-8000-00000000ca04',
              'level', 'orange')),
            tests.ev('e4000000-0000-4000-8000-000000000011', 'child_upserted', jsonb_build_object(
              'child_id', '00000000-0000-4000-8000-00000000ca04', 'birth_year', 2018))
          )) -> 0 ->> 'code'), 'consent_required', 'after withdrawal: screening refused');
select is((select count(*) from public.sync_event where result ->> 'code' = 'consent_required'
             and event_id in ('e4000000-0000-4000-8000-000000000008', 'e4000000-0000-4000-8000-000000000009',
                              'e4000000-0000-4000-8000-000000000010', 'e4000000-0000-4000-8000-000000000011'))::int, 4,
          'after withdrawal: screening, brushing, referral and profile updates are ALL refused');
select is((select count(*) from public.screening where child_id = '00000000-0000-4000-8000-00000000ca04')::int, 1,
          'no new screening stored after withdrawal');

-- RLS backstop: bypassing the RPC does not bypass consent
select throws_ok($$ insert into public.screening (id, child_id, recorded_by, screened_at, model_version, rules_version, level)
                    values ('54000000-0000-4000-8000-0000000000ff', '00000000-0000-4000-8000-00000000ca04',
                            '00000000-0000-4000-8000-0000000000a1', now(), '0.1.0', '0.1.0', 'green') $$,
                 '42501', null, 'direct INSERT without consent (b) is blocked by RLS');
select lives_ok($$ update public.child_profile set birth_year = 2010 where id = '00000000-0000-4000-8000-00000000ca04' $$,
                'direct UPDATE without consent (b) runs ...');
select is((select birth_year from public.child_profile where id = '00000000-0000-4000-8000-00000000ca04')::int, 2017,
          '... but changes nothing');
select throws_ok($$ update public.consent set text_version = 'forged' where child_id = '00000000-0000-4000-8000-00000000ca04' $$,
                 '42501', null, 'consent text/version cannot be rewritten');

-- Re-grant restores sync
select is((public.sync_events(jsonb_build_array(
            tests.ev('e4000000-0000-4000-8000-000000000012', 'consent_changed', jsonb_build_object(
              'child_id', '00000000-0000-4000-8000-00000000ca04', 'consent_type', 'b', 'action', 'grant',
              'text_version', 'consent-b-v2')),
            tests.ev('e4000000-0000-4000-8000-000000000013', 'brushing_recorded', jsonb_build_object(
              'brushing_id', '64000000-0000-4000-8000-000000000002', 'child_id', '00000000-0000-4000-8000-00000000ca04',
              'started_at', now(), 'duration_s', 130))
          )) -> 1 ->> 'status'), 'applied', 're-granting (b) with a new text version restores sync');
select is((select count(*) from public.consent where child_id = '00000000-0000-4000-8000-00000000ca04'
             and consent_type = 'b')::int, 2, 'consent history is kept (withdrawn row + new row)');

-- Withdrawal of (c) marks uploaded photos as withdrawn
select tests.as_owner();
do $$ begin
  insert into public.consent (child_id, granted_by, consent_type, text_version, granted_at)
  values ('00000000-0000-4000-8000-00000000ca04', '00000000-0000-4000-8000-0000000000a1', 'c', 'consent-c-v1', now());
  insert into public.image_upload (id, child_id, uploaded_by, object_path)
  values ('84000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-00000000ca04',
          '00000000-0000-4000-8000-0000000000a1',
          '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-00000000ca04/84000000-0000-4000-8000-000000000001.jpg');
end $$;
select tests.as_user('00000000-0000-4000-8000-0000000000a1');
select is((public.sync_events(jsonb_build_array(tests.ev('e4000000-0000-4000-8000-000000000014', 'consent_changed',
          jsonb_build_object('child_id', '00000000-0000-4000-8000-00000000ca04', 'consent_type', 'c',
                             'action', 'withdraw'))))) -> 0 ->> 'status', 'applied', 'withdrawal of (c) accepted');
select is((select status::text from public.image_upload where id = '84000000-0000-4000-8000-000000000001'),
          'withdrawn', 'photos of that child are marked withdrawn (removed later by retention-sweep)');

select tests.as_owner();
select * from finish();
rollback;
