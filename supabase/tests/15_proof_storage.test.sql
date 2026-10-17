-- ADR 017 §2.5: Storage bucket brushing_proofs (child INSERT only to its
-- registered path, guardian SELECT only for live proofs, no update/delete, no
-- admin read), proof RPCs (list / viewed / delete), consent (d) withdrawal,
-- and the proof sweeper service functions.
begin;
\ir _helpers.psql
select plan(37);

do $$ begin
  perform tests.make_child('00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000c5101', array['a', 'b', 'd']);
  perform tests.make_child('00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000c51b1', array['a', 'b', 'd']);
  perform tests.new_child_user('00000000-0000-4000-8000-0000000d5101', '00000000-0000-4000-8000-0000000000a1',
                               '00000000-0000-4000-8000-0000000c5101', 'anak.lima');
  perform tests.new_child_user('00000000-0000-4000-8000-0000000d51b1', '00000000-0000-4000-8000-0000000000b1',
                               '00000000-0000-4000-8000-0000000c51b1', 'anak.b');
end $$;

select tests.as_user('00000000-0000-4000-8000-0000000d5101');
do $$ begin
  perform public.sync_events(jsonb_build_array(
    tests.ev('e1500000-0000-4000-8000-000000000001', 'brushing_recorded', jsonb_build_object(
      'brushing_id', '61500000-0000-4000-8000-000000000001', 'child_id', '00000000-0000-4000-8000-0000000c5101',
      'started_at', now() - interval '20 minutes', 'duration_s', 120)),
    tests.ev('e1500000-0000-4000-8000-000000000002', 'brushing_recorded', jsonb_build_object(
      'brushing_id', '61500000-0000-4000-8000-000000000002', 'child_id', '00000000-0000-4000-8000-0000000c5101',
      'started_at', now() - interval '10 minutes', 'duration_s', 120)),
    tests.ev('e1500000-0000-4000-8000-000000000003', 'brushing_proof_registered', jsonb_build_object(
      'proof_id', 'b1500000-0000-4000-8000-000000000001', 'child_id', '00000000-0000-4000-8000-0000000c5101',
      'brushing_event_id', '61500000-0000-4000-8000-000000000001', 'size_bytes', 2000000, 'duration_s', 100, 'mime', 'video/mp4')),
    tests.ev('e1500000-0000-4000-8000-000000000004', 'brushing_proof_registered', jsonb_build_object(
      'proof_id', 'b1500000-0000-4000-8000-000000000002', 'child_id', '00000000-0000-4000-8000-0000000c5101',
      'brushing_event_id', '61500000-0000-4000-8000-000000000002', 'size_bytes', 2000000, 'duration_s', 100, 'mime', 'video/mp4'))));
end $$;
select tests.as_user('00000000-0000-4000-8000-0000000d51b1');
do $$ begin
  perform public.sync_events(jsonb_build_array(
    tests.ev('e1500000-0000-4000-8000-0000000000b1', 'brushing_recorded', jsonb_build_object(
      'brushing_id', '61500000-0000-4000-8000-0000000000b1', 'child_id', '00000000-0000-4000-8000-0000000c51b1',
      'started_at', now() - interval '20 minutes', 'duration_s', 120)),
    tests.ev('e1500000-0000-4000-8000-0000000000b2', 'brushing_proof_registered', jsonb_build_object(
      'proof_id', 'b1500000-0000-4000-8000-0000000000b1', 'child_id', '00000000-0000-4000-8000-0000000c51b1',
      'brushing_event_id', '61500000-0000-4000-8000-0000000000b1', 'size_bytes', 2000000, 'duration_s', 100, 'mime', 'video/mp4'))));
end $$;

-- ------------------------------------------------------------ child account uploads
select tests.as_user('00000000-0000-4000-8000-0000000d5101');
select lives_ok($$ insert into storage.objects (bucket_id, name, owner, metadata) values ('brushing_proofs',
  '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-0000000c5101/b1500000-0000-4000-8000-000000000001.mp4',
  '00000000-0000-4000-8000-0000000d5101', '{"size": 2000000, "mimetype": "video/mp4"}') $$,
  'child account uploads to its registered proof path');
select throws_ok($$ insert into storage.objects (bucket_id, name) values ('brushing_proofs',
  '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-0000000c5101/b1500000-0000-4000-8000-000000000099.mp4') $$,
  '42501', null, 'no upload without a registered proof');
select throws_ok($$ insert into storage.objects (bucket_id, name) values ('brushing_proofs',
  '00000000-0000-4000-8000-0000000000b1/00000000-0000-4000-8000-0000000c51b1/b1500000-0000-4000-8000-0000000000b1.mp4') $$,
  '42501', null, 'no upload to another child''s proof path');
select throws_ok($$ insert into storage.objects (bucket_id, name, metadata) values ('brushing_proofs',
  '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-0000000c5101/b1500000-0000-4000-8000-000000000002.mp4',
  '{"size": 2000000, "mimetype": "video/quicktime"}') $$,
  '42501', null, 'mimetype must match the registered proof');
select throws_ok($$ insert into storage.objects (bucket_id, name, metadata) values ('brushing_proofs',
  '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-0000000c5101/b1500000-0000-4000-8000-000000000002.mp4',
  '{"size": 11000000, "mimetype": "video/mp4"}') $$,
  '42501', null, 'objects over proof_max_bytes are refused');
select is_empty($$ select 1 from storage.objects where bucket_id = 'brushing_proofs' $$,
                'the child cannot read the bucket back, not even its own upload');
select throws_ok($$ delete from storage.objects where bucket_id = 'brushing_proofs' $$, '42501', null,
                 'the child cannot delete proof objects');
select tests.as_user('00000000-0000-4000-8000-0000000d51b1');
select throws_ok($$ insert into storage.objects (bucket_id, name) values ('brushing_proofs',
  '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-0000000c5101/b1500000-0000-4000-8000-000000000002.mp4') $$,
  '42501', null, 'another child account cannot fill someone else''s proof');

-- ------------------------------------------------------------ guardian A
select tests.as_user('00000000-0000-4000-8000-0000000000a1');
select is((select count(*) from storage.objects where bucket_id = 'brushing_proofs')::int, 1,
          'guardian can read (sign) the live proof objects of own child');
select throws_ok($$ insert into storage.objects (bucket_id, name) values ('brushing_proofs',
  '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-0000000c5101/b1500000-0000-4000-8000-000000000002.mp4') $$,
  '42501', null, 'guardians do not upload proofs');
select throws_ok($$ delete from storage.objects where bucket_id = 'brushing_proofs' $$, '42501', null,
                 'guardians cannot delete objects directly');
select results_eq($$ select id, object_path, object_present, status::text
                     from public.list_brushing_proofs('00000000-0000-4000-8000-0000000c5101') $$,
                  $$ values ('b1500000-0000-4000-8000-000000000002'::uuid,
                             '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-0000000c5101/b1500000-0000-4000-8000-000000000002.mp4'::text,
                             false, 'uploaded'::text),
                            ('b1500000-0000-4000-8000-000000000001'::uuid,
                             '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-0000000c5101/b1500000-0000-4000-8000-000000000001.mp4'::text,
                             true, 'uploaded'::text) $$,
                  'list_brushing_proofs: path for createSignedUrl + whether the upload arrived');
select is(public.mark_proof_viewed('b1500000-0000-4000-8000-000000000001') ->> 'status', 'viewed', 'mark_proof_viewed');
select lives_ok($$ select public.mark_proof_viewed('b1500000-0000-4000-8000-000000000001') $$, 'mark_proof_viewed is idempotent');
select is(public.delete_brushing_proof('b1500000-0000-4000-8000-000000000002') ->> 'status', 'deleted', 'delete_brushing_proof');
select is((select count(*) from public.list_brushing_proofs('00000000-0000-4000-8000-0000000c5101'))::int, 1,
          'deleted proofs disappear from the list at once');
select tests.as_owner();
select is((select count(*) from public.audit_log where action = 'brushing_proof_viewed')::int, 1,
          'the first view is audited once (hashed ids)');
select tests.as_user('00000000-0000-4000-8000-0000000d5101');
select throws_ok($$ insert into storage.objects (bucket_id, name) values ('brushing_proofs',
  '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-0000000c5101/b1500000-0000-4000-8000-000000000002.mp4') $$,
  '42501', null, 'a deleted proof cannot be uploaded any more');

select tests.as_user('00000000-0000-4000-8000-0000000000b1');
select is((select count(*) from storage.objects where bucket_id = 'brushing_proofs'
             and name like '00000000-0000-4000-8000-0000000000a1/%')::int, 0, 'B cannot see A''s proof objects');
select throws_ok($$ select * from public.list_brushing_proofs('00000000-0000-4000-8000-0000000c5101') $$,
                 '42501', null, 'B cannot list A''s proofs');
select tests.as_user('00000000-0000-4000-8000-0000000000e1');
select is((select count(*) from storage.objects where bucket_id = 'brushing_proofs')::int, 0,
          'admin has no read access to proof videos');

-- consent (d) withdrawal hides everything at once
select tests.as_user('00000000-0000-4000-8000-0000000000a1');
select is((public.sync_events(jsonb_build_array(tests.ev('e1500000-0000-4000-8000-000000000010', 'consent_changed',
          jsonb_build_object('child_id', '00000000-0000-4000-8000-0000000c5101', 'consent_type', 'd',
                             'action', 'withdraw'))))) -> 0 ->> 'status', 'applied', 'guardian withdraws consent (d)');
select ok((select count(*) from public.list_brushing_proofs('00000000-0000-4000-8000-0000000c5101')) = 0
          and (select count(*) from storage.objects where bucket_id = 'brushing_proofs') = 0
          and (select count(*) from public.brushing_proof) = 0,
          'after withdrawal: no list rows, no signed-URL access, no metadata');
select tests.as_owner();
select is((select status::text from public.brushing_proof where id = 'b1500000-0000-4000-8000-000000000001'), 'deleted',
          'withdrawal marks the live proofs deleted (sweeper removes the objects)');
select tests.as_anon();
select throws_ok($$ insert into storage.objects (bucket_id, name) values ('brushing_proofs',
  '00000000-0000-4000-8000-0000000000b1/00000000-0000-4000-8000-0000000c51b1/b1500000-0000-4000-8000-0000000000b1.mp4') $$,
  '42501', null, 'anon cannot upload');

-- ------------------------------------------------------------ sweeper (service_role)
select tests.as_service();
select results_eq($$ select proof_id from public.service_expired_proofs(100) order by proof_id $$,
                  $$ values ('b1500000-0000-4000-8000-000000000001'::uuid), ('b1500000-0000-4000-8000-000000000002'::uuid) $$,
                  'expired-proof list: deleted / consent-withdrawn proofs (not the live one of B)');
select public.service_mark_proof_object_removed('b1500000-0000-4000-8000-000000000001');
select is((select object_removed_at from public.brushing_proof where id = 'b1500000-0000-4000-8000-000000000001'), null,
          'marking is a no-op while the object still exists');
select tests.as_owner();
select set_config('storage.allow_delete_query', 'true', true);
delete from storage.objects where bucket_id = 'brushing_proofs' and name like '%b1500000-0000-4000-8000-000000000001%';
select set_config('storage.allow_delete_query', 'false', true);
select tests.as_service();
select public.service_mark_proof_object_removed('b1500000-0000-4000-8000-000000000001');
select public.service_mark_proof_object_removed('b1500000-0000-4000-8000-000000000002');
select ok((select object_removed_at is not null and status = 'deleted' from public.brushing_proof
           where id = 'b1500000-0000-4000-8000-000000000001'), 'after the Storage removal the proof is marked removed');
select ok((select object_removed_at is not null and status = 'deleted' from public.brushing_proof
           where id = 'b1500000-0000-4000-8000-000000000002'), 'a never-uploaded proof is marked removed too');
select is((public.service_retention_sweep_db() ->> 'brushing_proof_rows_deleted')::int, 2,
          'daily retention purges proof rows whose object is gone');
select is((select count(*) from public.brushing_proof where child_id = '00000000-0000-4000-8000-0000000c5101')::int, 0,
          '... file and row are gone');
select tests.as_owner();
insert into storage.objects (bucket_id, name, created_at) values ('brushing_proofs',
  '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-0000000c5101/b1500000-0000-4000-8000-0000000000ff.mp4',
  now() - interval '2 hours');
update public.brushing_proof set uploaded_at = now() - interval '8 days', expires_at = now() - interval '1 day'
 where id = 'b1500000-0000-4000-8000-0000000000b1';
select tests.as_service();
select ok(exists (select 1 from public.service_expired_proofs(100)
                  where proof_id is null and object_path like '%b1500000-0000-4000-8000-0000000000ff.mp4'),
          'orphan objects (no row) are returned with proof_id null');
select ok(exists (select 1 from public.service_expired_proofs(100) where proof_id = 'b1500000-0000-4000-8000-0000000000b1'),
          'proofs past proof_retention_days are returned');
select is((public.service_retention_sweep_db() ->> 'brushing_proof_expired')::int, 1,
          'daily retention marks overdue proofs expired');
select tests.as_user('00000000-0000-4000-8000-0000000000b1');
select throws_ok($$ select * from public.service_expired_proofs(10) $$, '42501', null, 'clients cannot call the sweeper RPCs');

select tests.as_owner();
select results_eq($$ select public, file_size_limit, allowed_mime_types from storage.buckets where id = 'brushing_proofs' $$,
                  $$ values (false, 10485760::bigint, array['video/mp4', 'video/quicktime']::text[]) $$,
                  'brushing_proofs: private, 10 MiB, mp4/quicktime only');
select is_empty($$ select policyname from pg_policies
                   where schemaname = 'storage' and tablename = 'objects'
                     and policyname like 'dentisai_brushing_proofs%' and cmd not in ('INSERT', 'SELECT') $$,
                'brushing_proofs has no update/delete policies');

select * from finish();
rollback;
