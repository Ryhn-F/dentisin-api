-- ADR 017 §2.5/2.6: brushing_streak (local days in Asia/Jakarta, gaps, minimum
-- duration, midnight edges) and child_history (keyset pagination with ties,
-- kinds, role filtering, no PII), list_children summary.
begin;
\ir _helpers.psql
select plan(29);

do $$
declare
  t date := (now() at time zone 'Asia/Jakarta')::date;
  c41 uuid := '00000000-0000-4000-8000-0000000c4101';
  a uuid := '00000000-0000-4000-8000-0000000000a1';
begin
  perform tests.make_child(a, c41, array['a', 'b', 'd']);
  perform tests.make_child(a, '00000000-0000-4000-8000-0000000c4102');
  perform tests.make_child(a, '00000000-0000-4000-8000-0000000c4103');
  perform tests.make_child(a, '00000000-0000-4000-8000-0000000c4104', array['a', 'b', 'd']);
  perform tests.new_child_user('00000000-0000-4000-8000-0000000d4101', a, c41, 'anak.empat');
  perform tests.new_child_user('00000000-0000-4000-8000-0000000d4104', a, '00000000-0000-4000-8000-0000000c4104', 'anak.history');
  -- c41: counted T, T-1 (23:59:59 WIB), T-2 (61 s; a 30 s one does not count),
  --      gap at T-3 (59 s only), counted T-4 .. T-7 (run of 4)
  insert into public.brushing_event (id, child_id, recorded_by, started_at, duration_s) values
    ('61400000-0000-4000-8000-000000000001', c41, a, (t + time '00:00:30') at time zone 'Asia/Jakarta', 120),
    ('61400000-0000-4000-8000-000000000002', c41, a, ((t - 1) + time '23:59:59') at time zone 'Asia/Jakarta', 90),
    ('61400000-0000-4000-8000-000000000003', c41, a, ((t - 2) + time '00:01:00') at time zone 'Asia/Jakarta', 30),
    ('61400000-0000-4000-8000-000000000004', c41, a, ((t - 2) + time '00:30:00') at time zone 'Asia/Jakarta', 61),
    ('61400000-0000-4000-8000-000000000005', c41, a, ((t - 3) + time '12:00:00') at time zone 'Asia/Jakarta', 59),
    ('61400000-0000-4000-8000-000000000006', c41, a, ((t - 4) + time '12:00:00') at time zone 'Asia/Jakarta', 300),
    ('61400000-0000-4000-8000-000000000007', c41, a, ((t - 5) + time '12:00:00') at time zone 'Asia/Jakarta', 300),
    ('61400000-0000-4000-8000-000000000008', c41, a, ((t - 6) + time '12:00:00') at time zone 'Asia/Jakarta', 300),
    ('61400000-0000-4000-8000-000000000009', c41, a, ((t - 7) + time '12:00:00') at time zone 'Asia/Jakarta', 300),
    -- c42: yesterday and the day before, nothing today
    ('61400000-0000-4000-8000-000000000021', '00000000-0000-4000-8000-0000000c4102', a,
     ((t - 1) + time '12:00:00') at time zone 'Asia/Jakarta', 120),
    ('61400000-0000-4000-8000-000000000022', '00000000-0000-4000-8000-0000000c4102', a,
     ((t - 2) + time '12:00:00') at time zone 'Asia/Jakarta', 120);

  -- c44 timeline (relative to now()): request + proof at now (tie), screening +
  -- referral at -1 h (tie), brushing at -2 h, -3 h, -26 h
  insert into public.screening (id, child_id, recorded_by, screened_at, model_version, rules_version, level, findings)
  values ('51400000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-0000000c4104', a, now() - interval '1 hour',
          '1.0.0', '1.0.0', 'orange', '[{"class": "caries_primary", "confidence": 0.8}]');
  insert into public.referral (id, child_id, screening_id, created_by, level, reason_codes, referred_at)
  values ('71400000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-0000000c4104',
          '51400000-0000-4000-8000-000000000001', a, 'orange', array['caries_primary'], now() - interval '1 hour');
  insert into public.brushing_event (id, child_id, recorded_by, started_at, duration_s) values
    ('61400000-0000-4000-8000-000000000041', '00000000-0000-4000-8000-0000000c4104', a, now() - interval '2 hours', 120),
    ('61400000-0000-4000-8000-000000000042', '00000000-0000-4000-8000-0000000c4104', a, now() - interval '3 hours', 40),
    ('61400000-0000-4000-8000-000000000043', '00000000-0000-4000-8000-0000000c4104', a, now() - interval '26 hours', 100);
  insert into public.screening_request (id, child_id, requested_by, reason_code, pain_level)
  values ('a1400000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-0000000c4104',
          '00000000-0000-4000-8000-0000000d4104', 'swelling', 4);
  insert into public.brushing_proof (id, child_id, brushing_event_id, uploaded_by, object_path, mime, size_bytes, duration_s)
  values ('b1400000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-0000000c4104',
          '61400000-0000-4000-8000-000000000041', '00000000-0000-4000-8000-0000000d4104',
          '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-0000000c4104/b1400000-0000-4000-8000-000000000001.mov',
          'video/quicktime', 3000000, 100);
end
$$;

-- ------------------------------------------------------------ streak
select tests.as_user('00000000-0000-4000-8000-0000000000a1');
select public.brushing_streak('00000000-0000-4000-8000-0000000c4101')::text as s41 \gset
select results_eq(format($$ select (%1$L::jsonb ->> 'current')::int, (%1$L::jsonb ->> 'longest')::int,
                                   (%1$L::jsonb ->> 'today_done')::boolean, (%1$L::jsonb ->> 'last_day')::date $$, :'s41'),
                  $$ values (3, 4, true, (now() at time zone 'Asia/Jakarta')::date) $$,
                  'streak: current 3 (T, T-1, T-2), longest 4 (T-7..T-4), today done');
select ok(jsonb_array_length(:'s41'::jsonb -> 'week') = 7
          and (:'s41'::jsonb -> 'week' -> 0 ->> 'date')::date = (now() at time zone 'Asia/Jakarta')::date - 6
          and (:'s41'::jsonb -> 'week' -> 6 ->> 'date')::date = (now() at time zone 'Asia/Jakarta')::date,
          'week = 7 local days, oldest first, ending today');
select ok((:'s41'::jsonb -> 'week' -> 3 ->> 'done')::boolean = false
          and (:'s41'::jsonb -> 'week' -> 4 ->> 'brushings')::int = 1
          and (:'s41'::jsonb -> 'week' -> 5 ->> 'brushings')::int = 1
          and (:'s41'::jsonb -> 'week' -> 6 ->> 'brushings')::int = 1,
          'midnight edges in WIB: 23:59:59 counts for that day, 00:00:30 for the next; < 60 s does not count');
select is((:'s41'::jsonb ->> 'min_duration_s') || '|' || (:'s41'::jsonb ->> 'tz'), '60|Asia/Jakarta',
          'reports min_duration_s and tz');
select tests.as_owner();
update public.app_setting set value = '30' where key = 'streak_min_duration_s';
select tests.as_user('00000000-0000-4000-8000-0000000000a1');
select is((public.brushing_streak('00000000-0000-4000-8000-0000000c4101') ->> 'current')::int
          + (public.brushing_streak('00000000-0000-4000-8000-0000000c4101') ->> 'longest')::int, 16,
          'streak_min_duration_s is read from app_setting (30 s closes the gap: 8 + 8)');
select tests.as_owner();
update public.app_setting set value = '60' where key = 'streak_min_duration_s';
select tests.as_user('00000000-0000-4000-8000-0000000000a1');
select is((public.brushing_streak('00000000-0000-4000-8000-0000000c4102') ->> 'current') || '|'
          || (public.brushing_streak('00000000-0000-4000-8000-0000000c4102') ->> 'today_done'), '2|false',
          'not brushed today yet: the streak ending yesterday still counts');
select is((public.brushing_streak('00000000-0000-4000-8000-0000000c4103') ->> 'current') || '|'
          || (public.brushing_streak('00000000-0000-4000-8000-0000000c4103') ->> 'longest') || '|'
          || coalesce(public.brushing_streak('00000000-0000-4000-8000-0000000c4103') ->> 'last_day', 'null'), '0|0|null',
          'no brushing: zeros');
select throws_ok($$ select public.brushing_streak('00000000-0000-4000-8000-0000000c4101', 'Mars/Olympus') $$,
                 '22023', null, 'unknown time zone refused');
select lives_ok($$ select public.brushing_streak('00000000-0000-4000-8000-0000000c4101', 'UTC') $$,
                'any pg_timezone_names zone accepted');
select tests.as_user('00000000-0000-4000-8000-0000000d4101');
select is((public.brushing_streak('00000000-0000-4000-8000-0000000c4101') ->> 'current')::int, 3,
          'child account reads its own streak');
select throws_ok($$ select public.brushing_streak('00000000-0000-4000-8000-0000000c4102') $$,
                 '42501', null, 'child account cannot read a sibling''s streak');

-- ------------------------------------------------------------ history
select tests.as_user('00000000-0000-4000-8000-0000000000a1');
select public.child_history('00000000-0000-4000-8000-0000000c4104')::text as h_all \gset
select ok(jsonb_array_length(:'h_all'::jsonb -> 'items') = 7 and :'h_all'::jsonb -> 'next_before' = 'null'::jsonb,
          'full history: 7 items, no next page');
select is((select array_agg(distinct x ->> 'kind' order by x ->> 'kind') from jsonb_array_elements(:'h_all'::jsonb -> 'items') x),
          array['brushing', 'brushing_proof', 'referral', 'screening', 'screening_request'], 'owner sees all five kinds');
select public.child_history('00000000-0000-4000-8000-0000000c4104', null, 1)::text as h_p1 \gset
select is((select array_agg(x ->> 'kind' order by x ->> 'kind') from jsonb_array_elements(:'h_p1'::jsonb -> 'items') x),
          array['brushing_proof', 'screening_request'], 'page 1 (limit 1) keeps both items of the boundary timestamp');
select ok((:'h_p1'::jsonb ->> 'next_before')::timestamptz = now(), 'page 1 next_before = boundary timestamp');
select public.child_history('00000000-0000-4000-8000-0000000c4104', (:'h_p1'::jsonb ->> 'next_before')::timestamptz, 1)::text as h_p2 \gset
select ok((select array_agg(x ->> 'kind' order by x ->> 'kind') from jsonb_array_elements(:'h_p2'::jsonb -> 'items') x)
            = array['referral', 'screening']
          and (:'h_p2'::jsonb ->> 'next_before')::timestamptz = now() - interval '1 hour',
          'page 2: screening + referral (tie), next_before = -1 h');
select public.child_history('00000000-0000-4000-8000-0000000c4104', (:'h_p2'::jsonb ->> 'next_before')::timestamptz, 2)::text as h_p3 \gset
select ok((select array_agg(x ->> 'id' order by x ->> 'occurred_at' desc) from jsonb_array_elements(:'h_p3'::jsonb -> 'items') x)
            = array['61400000-0000-4000-8000-000000000041', '61400000-0000-4000-8000-000000000042']
          and (:'h_p3'::jsonb ->> 'next_before')::timestamptz = now() - interval '3 hours',
          'page 3: two brushings, newest first');
select public.child_history('00000000-0000-4000-8000-0000000c4104', (:'h_p3'::jsonb ->> 'next_before')::timestamptz, 2)::text as h_p4 \gset
select ok(jsonb_array_length(:'h_p4'::jsonb -> 'items') = 1 and :'h_p4'::jsonb -> 'next_before' = 'null'::jsonb,
          'last page: one item, next_before null');
select is(jsonb_array_length(public.child_history('00000000-0000-4000-8000-0000000c4104', null, 30, array['screening']) -> 'items'), 1,
          'p_kinds filters');
select is(jsonb_array_length(public.child_history('00000000-0000-4000-8000-0000000c4104', null, 30, null,
                                                  now() - interval '150 minutes') -> 'items'), 5,
          'p_from filters (inclusive lower bound)');
select throws_ok($$ select public.child_history('00000000-0000-4000-8000-0000000c4104', null, 30, array['photo']) $$,
                 '22023', null, 'unknown kind refused');
select is(jsonb_array_length(public.child_history('00000000-0000-4000-8000-0000000c4104', null, 1000) -> 'items'), 7,
          'p_limit is capped, not an error');
select ok((select (x -> 'data' ->> 'counts_for_streak')::boolean = false from jsonb_array_elements(:'h_all'::jsonb -> 'items') x
           where x ->> 'id' = '61400000-0000-4000-8000-000000000042')
          and (select (x -> 'data' ->> 'has_proof')::boolean from jsonb_array_elements(:'h_all'::jsonb -> 'items') x
               where x ->> 'id' = '61400000-0000-4000-8000-000000000041'),
          'brushing data: counts_for_streak and has_proof');
select ok(:'h_all'::text !~* '(\.mov|\.mp4|object_path|anak\.history|@|recorded_by|requested_by|00000000-0000-4000-8000-0000000000a1|00000000-0000-4000-8000-0000000d4104)',
          'history data carries no storage path, username, e-mail or account ids');
select results_eq($$ select last_screening_level::text, open_requests, unviewed_proofs, username
                     from public.list_children() where child_id = '00000000-0000-4000-8000-0000000c4104' $$,
                  $$ values ('orange'::text, 1, 1, 'anak.history'::text) $$, 'list_children summary for one child');

select tests.as_user('00000000-0000-4000-8000-0000000d4104');
select is((select array_agg(distinct x ->> 'kind' order by x ->> 'kind')
           from jsonb_array_elements(public.child_history('00000000-0000-4000-8000-0000000c4104') -> 'items') x),
          array['brushing', 'brushing_proof', 'screening_request'], 'child account history: brushing, requests, proofs only');
select is(jsonb_array_length(public.child_history('00000000-0000-4000-8000-0000000c4104', null, 30,
                                                  array['screening', 'referral']) -> 'items'), 0,
          'child account cannot pull screening/referral items even when asked');

select tests.as_user('00000000-0000-4000-8000-0000000000b1');
select throws_ok($$ select public.child_history('00000000-0000-4000-8000-0000000c4104') $$, '42501', null,
                 'another guardian cannot read the history');
select tests.as_owner();
update public.child_account set disabled_at = now() where user_id = '00000000-0000-4000-8000-0000000d4104';
select tests.as_user('00000000-0000-4000-8000-0000000d4104');
select throws_ok($$ select public.child_history('00000000-0000-4000-8000-0000000c4104') $$, '42501', null,
                 'a disabled child account loses its history at once');

select tests.as_owner();
select * from finish();
rollback;
