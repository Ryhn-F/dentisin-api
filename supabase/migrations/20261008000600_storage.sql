-- =============================================================================
-- DentisAI · 0600 Storage buckets and policies (backend/CLAUDE.md §6, FR-25, FR-101)
-- =============================================================================
-- manifests      PUBLIC read (signed manifest.json/.sig, model, rules). Writes
--                only by service_role (team PC) or an admin account.
-- dataset_photos PRIVATE. Users may only INSERT (never select/update/delete)
--                under '<auth.uid()>/<child_id>/<uuid>.jpg', only when the
--                child has active consent (c) AND feature flag
--                dataset_contribution is ON (default OFF until KEPK approval).
--                JPEG only, <= 500 KiB (bucket config is the hard limit that
--                the Storage API enforces; the policy re-checks metadata when
--                present). Removal happens only through the withdrawal /
--                retention / right-to-delete path (Edge Functions with
--                service_role). Bulk read: service_role from the team PC only.
-- Project-wide upload cap (config.toml storage.file_size_limit) = 50MiB.

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values
  ('manifests', 'manifests', true, 52428800,
   array['application/json', 'application/octet-stream', 'text/plain']),
  ('dataset_photos', 'dataset_photos', false, 512000,
   array['image/jpeg'])
on conflict (id) do update
  set public = excluded.public,
      file_size_limit = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;

-- Policy predicate for dataset photo uploads (evaluated with caller privileges).
create or replace function private.dataset_upload_allowed(p_name text, p_metadata jsonb)
returns boolean
language plpgsql
stable
security invoker
set search_path = ''
as $$
declare
  v_parts text[];
  v_child uuid;
begin
  if (select auth.uid()) is null or p_name is null then
    return false;
  end if;
  v_parts := string_to_array(p_name, '/');
  if array_length(v_parts, 1) <> 3
     or v_parts[1] <> (select auth.uid())::text
     or v_parts[3] !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.jpe?g$' then
    return false;
  end if;
  v_child := private.try_uuid(v_parts[2]);
  if v_child is null or v_parts[2] <> v_child::text then
    return false;
  end if;
  if not private.flag_enabled('dataset_contribution')
     or not private.can_access_child(v_child)
     or not private.has_active_consent(v_child, 'c')
     or private.deletion_pending(v_child) then
    return false;
  end if;
  -- Metadata may be absent at the RLS check of a plain upload; when present it
  -- must agree with the bucket limits.
  if p_metadata is not null then
    if p_metadata ? 'size' and (p_metadata ->> 'size') ~ '^[0-9]+$'
       and (p_metadata ->> 'size')::bigint > 512000 then
      return false;
    end if;
    if p_metadata ? 'mimetype' and p_metadata ->> 'mimetype' <> 'image/jpeg' then
      return false;
    end if;
  end if;
  return true;
end
$$;

revoke all on function private.dataset_upload_allowed(text, jsonb) from public, anon;
grant execute on function private.dataset_upload_allowed(text, jsonb) to authenticated, service_role;

-- dataset_photos: INSERT only, own prefix, consent (c) + flag -----------------
drop policy if exists dentisai_dataset_photos_insert on storage.objects;
create policy dentisai_dataset_photos_insert on storage.objects
  for insert to authenticated
  with check (
    bucket_id = 'dataset_photos'
    and private.dataset_upload_allowed(name, metadata)
  );
-- Intentionally NO select/update/delete policies for dataset_photos.

-- manifests: admin accounts may manage; everyone reads through the public URL --
drop policy if exists dentisai_manifests_admin_select on storage.objects;
create policy dentisai_manifests_admin_select on storage.objects
  for select to authenticated
  using (bucket_id = 'manifests' and private.is_admin());

drop policy if exists dentisai_manifests_admin_insert on storage.objects;
create policy dentisai_manifests_admin_insert on storage.objects
  for insert to authenticated
  with check (bucket_id = 'manifests' and private.is_admin());

drop policy if exists dentisai_manifests_admin_update on storage.objects;
create policy dentisai_manifests_admin_update on storage.objects
  for update to authenticated
  using (bucket_id = 'manifests' and private.is_admin())
  with check (bucket_id = 'manifests' and private.is_admin());

drop policy if exists dentisai_manifests_admin_delete on storage.objects;
create policy dentisai_manifests_admin_delete on storage.objects
  for delete to authenticated
  using (bucket_id = 'manifests' and private.is_admin());

-- ---------------------------------------------------------------------------
-- Quota monitoring (admin / service_role). Free plan: 1 GB storage, 500 MB DB.
-- ---------------------------------------------------------------------------
create or replace function public.admin_storage_usage()
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_buckets jsonb;
  v_total bigint;
begin
  if not (private.is_admin() or coalesce(auth.role(), '') = 'service_role') then
    raise exception 'forbidden' using errcode = '42501';
  end if;
  select coalesce(jsonb_agg(jsonb_build_object(
           'bucket_id', b.bucket_id, 'objects', b.objects, 'bytes', b.bytes) order by b.bucket_id), '[]'::jsonb),
         coalesce(sum(b.bytes), 0)
    into v_buckets, v_total
  from (
    select o.bucket_id,
           count(*) as objects,
           coalesce(sum(case when (o.metadata ->> 'size') ~ '^[0-9]+$'
                             then (o.metadata ->> 'size')::bigint else 0 end), 0)::bigint as bytes
    from storage.objects o
    group by o.bucket_id
  ) b;
  return jsonb_build_object(
    'buckets', v_buckets,
    'storage_bytes', v_total,
    'storage_quota_bytes', 1073741824,
    'storage_pct', round(100.0 * v_total / 1073741824, 2),
    'db_bytes', pg_catalog.pg_database_size(current_database()),
    'db_quota_bytes', 524288000,
    'db_pct', round(100.0 * pg_catalog.pg_database_size(current_database()) / 524288000, 2),
    'dataset_photos_estimated_capacity_left',
      greatest(0, (1073741824 - v_total) / 512000)
  );
end
$$;

comment on function public.admin_storage_usage() is
  'Free-plan quota monitor (1 GB storage / 500 MB DB). Admin or service_role only. Quotas [PERLU VERIFIKASI] against supabase.com/pricing.';

revoke all on function public.admin_storage_usage() from public, anon;
grant execute on function public.admin_storage_usage() to authenticated, service_role;
