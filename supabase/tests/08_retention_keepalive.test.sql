-- Retention (image TTL / withdrawn photos / sync ledger) and keepalive.
begin;
\ir _helpers.psql
select plan(11);

do $$ begin
  perform tests.make_child('00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-00000000ca09', array['a', 'b', 'c']);
  -- expired photo (object still in storage) and a withdrawn photo (object already gone)
  insert into public.image_upload (id, child_id, uploaded_by, object_path) values
    ('89000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-00000000ca09', '00000000-0000-4000-8000-0000000000a1',
     '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-00000000ca09/89000000-0000-4000-8000-000000000001.jpg'),
    ('89000000-0000-4000-8000-000000000002', '00000000-0000-4000-8000-00000000ca09', '00000000-0000-4000-8000-0000000000a1',
     '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-00000000ca09/89000000-0000-4000-8000-000000000002.jpg'),
    ('89000000-0000-4000-8000-000000000003', '00000000-0000-4000-8000-00000000ca09', '00000000-0000-4000-8000-0000000000a1',
     '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-00000000ca09/89000000-0000-4000-8000-000000000003.jpg');
  update public.image_upload set uploaded_at = now() - interval '400 days', expires_at = now() - interval '35 days'
   where id = '89000000-0000-4000-8000-000000000001';
  update public.image_upload set status = 'withdrawn', withdrawn_at = now()
   where id = '89000000-0000-4000-8000-000000000002';
  insert into storage.objects (bucket_id, name) values
    ('dataset_photos', '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-00000000ca09/89000000-0000-4000-8000-000000000001.jpg');
  insert into public.sync_event (event_id, submitted_by, event_type, payload_sha256, result, received_at) values
    ('e9000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-0000000000a1', 'brushing_recorded',
     repeat('a', 64), '{"status": "applied"}', now() - interval '400 days'),
    ('e9000000-0000-4000-8000-000000000002', '00000000-0000-4000-8000-0000000000a1', 'brushing_recorded',
     repeat('b', 64), '{"status": "applied"}', now() - interval '1 day');
end $$;

select tests.as_service();
select results_eq($$ select upload_id from public.service_retention_candidates(100) order by upload_id $$,
                  $$ values ('89000000-0000-4000-8000-000000000001'::uuid), ('89000000-0000-4000-8000-000000000002'::uuid) $$,
                  'candidates = expired + withdrawn photos (not the fresh one)');
select is(public.service_mark_images_deleted(array['89000000-0000-4000-8000-000000000001',
                                                   '89000000-0000-4000-8000-000000000002']::uuid[]), 1,
          'only photos whose object is really gone are marked deleted');
select is((select status::text from public.image_upload where id = '89000000-0000-4000-8000-000000000001'), 'uploaded',
          'expired photo stays pending until its object is removed');

select tests.as_owner();
select set_config('storage.allow_delete_query', 'true', true);
delete from storage.objects where name like '%89000000-0000-4000-8000-000000000001%';
select set_config('storage.allow_delete_query', 'false', true);
select tests.as_service();
select is(public.service_mark_images_deleted(array['89000000-0000-4000-8000-000000000001']::uuid[]), 1,
          'after the Storage removal the expired photo is marked deleted');
select results_eq($$ select status::text, deleted_at is not null from public.image_upload
                     where id in ('89000000-0000-4000-8000-000000000001', '89000000-0000-4000-8000-000000000002')
                     order by id $$,
                  $$ values ('deleted'::text, true), ('deleted', true) $$, 'status + deleted_at recorded');
select is((public.service_retention_sweep_db() ->> 'sync_event_deleted')::int, 1,
          'ledger rows older than sync_event_retention_days are purged');
select ok(exists (select 1 from public.sync_event where event_id = 'e9000000-0000-4000-8000-000000000002'),
          'recent ledger rows are kept');

select tests.as_user('00000000-0000-4000-8000-0000000000a1');
select throws_ok($$ select * from public.service_retention_candidates(10) $$, '42501', null,
                 'clients cannot call retention RPCs');
select is(public.keepalive(), 1, 'keepalive() for authenticated');
select tests.as_anon();
select is(public.keepalive(), 1, 'keepalive() for anon (external cron ping)');
select throws_ok($$ select public.service_retention_sweep_db() $$, '42501', null, 'anon cannot call service RPCs');

select tests.as_owner();
select * from finish();
rollback;
