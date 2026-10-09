-- Storage: dataset_photos is insert-only under own prefix with consent (c) AND
-- the dataset_contribution flag; no reads/updates/deletes for users; manifests
-- writable only by admin/service_role. Simulates the rows the Storage API
-- writes into storage.objects (RLS is what the Storage API enforces).
begin;
\ir _helpers.psql
select plan(22);

do $$ begin
  perform tests.make_child('00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-00000000ca06');
  perform tests.make_child('00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-00000000cb06', array['a', 'b', 'c']);
end $$;

select tests.as_user('00000000-0000-4000-8000-0000000000a1');
select throws_ok($$ insert into storage.objects (bucket_id, name, owner, metadata) values ('dataset_photos',
  '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-00000000ca06/86000000-0000-4000-8000-000000000001.jpg',
  '00000000-0000-4000-8000-0000000000a1', '{"size": 120000, "mimetype": "image/jpeg"}') $$,
  '42501', null, 'no upload without consent (c) (and flag off)');

select tests.as_owner();
insert into public.consent (child_id, granted_by, consent_type, text_version, granted_at)
values ('00000000-0000-4000-8000-00000000ca06', '00000000-0000-4000-8000-0000000000a1', 'c', 'consent-c-v1', now());

select tests.as_user('00000000-0000-4000-8000-0000000000a1');
select throws_ok($$ insert into storage.objects (bucket_id, name, owner, metadata) values ('dataset_photos',
  '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-00000000ca06/86000000-0000-4000-8000-000000000001.jpg',
  '00000000-0000-4000-8000-0000000000a1', '{"size": 120000, "mimetype": "image/jpeg"}') $$,
  '42501', null, 'consent (c) alone is not enough while dataset_contribution is OFF');
select is((public.sync_events(jsonb_build_array(tests.ev('e6000000-0000-4000-8000-000000000001', 'image_upload_registered',
          jsonb_build_object('upload_id', '86000000-0000-4000-8000-000000000001',
                             'child_id', '00000000-0000-4000-8000-00000000ca06',
                             'object_path', '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-00000000ca06/86000000-0000-4000-8000-000000000001.jpg')))))
          -> 0 ->> 'code', 'feature_disabled', 'image registration refused while the flag is OFF');

select tests.as_owner();
update public.feature_flag set enabled = true, approval_ref = 'TEST-KEPK-0' where key = 'dataset_contribution';

select tests.as_user('00000000-0000-4000-8000-0000000000a1');
select lives_ok($$ insert into storage.objects (bucket_id, name, owner, metadata) values ('dataset_photos',
  '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-00000000ca06/86000000-0000-4000-8000-000000000001.jpg',
  '00000000-0000-4000-8000-0000000000a1', '{"size": 120000, "mimetype": "image/jpeg"}') $$,
  'upload allowed: own prefix + consent (c) + flag ON');
select lives_ok($$ insert into storage.objects (bucket_id, name, owner) values ('dataset_photos',
  '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-00000000ca06/86000000-0000-4000-8000-000000000002.jpeg',
  '00000000-0000-4000-8000-0000000000a1') $$,
  'upload allowed when metadata is not yet known (bucket limits enforced by Storage API)');
select throws_ok($$ insert into storage.objects (bucket_id, name) values ('dataset_photos',
  '00000000-0000-4000-8000-0000000000b1/00000000-0000-4000-8000-00000000ca06/86000000-0000-4000-8000-000000000003.jpg') $$,
  '42501', null, 'cannot write under another user''s prefix');
select throws_ok($$ insert into storage.objects (bucket_id, name) values ('dataset_photos',
  '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-00000000cb06/86000000-0000-4000-8000-000000000004.jpg') $$,
  '42501', null, 'cannot upload photos of a child the user does not own');
select throws_ok($$ insert into storage.objects (bucket_id, name) values ('dataset_photos',
  '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-00000000ca06/86000000-0000-4000-8000-000000000005.png') $$,
  '42501', null, 'only .jpg/.jpeg names');
select throws_ok($$ insert into storage.objects (bucket_id, name) values ('dataset_photos',
  '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-00000000ca06/budi_kelas4.jpg') $$,
  '42501', null, 'file names must be UUIDs');
select throws_ok($$ insert into storage.objects (bucket_id, name, metadata) values ('dataset_photos',
  '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-00000000ca06/86000000-0000-4000-8000-000000000006.jpg',
  '{"size": 600000, "mimetype": "image/jpeg"}') $$,
  '42501', null, 'objects over 500 KiB are refused');
select throws_ok($$ insert into storage.objects (bucket_id, name, metadata) values ('dataset_photos',
  '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-00000000ca06/86000000-0000-4000-8000-000000000007.jpg',
  '{"size": 1000, "mimetype": "image/png"}') $$,
  '42501', null, 'non-JPEG mimetypes are refused');

select is_empty($$ select 1 from storage.objects where bucket_id = 'dataset_photos' $$,
                'users cannot read the dataset bucket, not even their own uploads');
select lives_ok($$ update storage.objects set name = name || 'x' where bucket_id = 'dataset_photos' $$,
                'update attempt runs ...');
select throws_ok($$ delete from storage.objects where bucket_id = 'dataset_photos' $$, '42501', null,
                 'users cannot delete dataset objects');

select is((public.sync_events(jsonb_build_array(tests.ev('e6000000-0000-4000-8000-000000000002', 'image_upload_registered',
          jsonb_build_object('upload_id', '86000000-0000-4000-8000-000000000001',
                             'child_id', '00000000-0000-4000-8000-00000000ca06',
                             'object_path', '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-00000000ca06/86000000-0000-4000-8000-000000000001.jpg')))))
          -> 0 ->> 'status', 'applied', 'image metadata registered after the upload');
select ok((select expires_at between now() + interval '364 days' and now() + interval '366 days'
           from public.image_upload where id = '86000000-0000-4000-8000-000000000001'),
          'server sets the TTL (expires_at = now + image_upload_ttl_days)');
select is((public.sync_events(jsonb_build_array(tests.ev('e6000000-0000-4000-8000-000000000003', 'image_upload_registered',
          jsonb_build_object('upload_id', '86000000-0000-4000-8000-000000000009',
                             'child_id', '00000000-0000-4000-8000-00000000ca06',
                             'object_path', '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-00000000ca06/86000000-0000-4000-8000-000000000009.jpg')))))
          -> 0 ->> 'code', 'schema_invalid', 'cannot register metadata for an object that was never uploaded');

select tests.as_service();
select is((select count(*) from storage.objects where bucket_id = 'dataset_photos' and name ~ '\.jpe?g$')::int, 2,
          'service_role (team PC) can list the dataset; object names unchanged by the user update');

-- manifests -------------------------------------------------------------------
select tests.as_user('00000000-0000-4000-8000-0000000000a1');
select throws_ok($$ insert into storage.objects (bucket_id, name) values ('manifests', 'manifest.json') $$,
                 '42501', null, 'guardians cannot write manifests');
select tests.as_user('00000000-0000-4000-8000-0000000000e1');
select lives_ok($$ insert into storage.objects (bucket_id, name) values ('manifests', 'manifest.json') $$,
                'admin can publish a manifest');
select is((public.admin_storage_usage() ->> 'storage_quota_bytes')::bigint, 1073741824::bigint,
          'admin quota monitor reports the 1 GB Free-plan storage quota');
select tests.as_anon();
select throws_ok($$ insert into storage.objects (bucket_id, name) values ('dataset_photos',
  '00000000-0000-4000-8000-0000000000a1/00000000-0000-4000-8000-00000000ca06/86000000-0000-4000-8000-000000000008.jpg') $$,
  '42501', null, 'anon cannot upload');

select tests.as_owner();
select * from finish();
rollback;
