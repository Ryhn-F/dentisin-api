-- =============================================================================
-- DentisAI · 0700 Right-to-delete (FR-102) and retention
-- =============================================================================
-- Flow
--   1. Client calls public.request_deletion('child', <child_id>) or
--      public.request_deletion('account'). Sync for the subject stops at once
--      (deletion_pending).
--   2. Storage objects (dataset_photos) can only be removed through the Storage
--      API, so the Edge Function `process-deletion` (service_role) lists them
--      via service_deletion_storage_paths(), removes them, then calls
--      service_execute_deletion(request_id, removed_count).
--   3. pg_cron job `dentisai-process-deletions` (every 15 min) completes every
--      request that has NO remaining storage objects directly in SQL; requests
--      that still have objects wait for step 2 (status 'processing',
--      failure_code 'awaiting_storage_purge').
--   4. Completion deletes the child (cascades to consent, screening, referral,
--      brushing_event, image_upload, roster membership, sync ledger) or the
--      whole account (all owned children, rosters, guardian, roles, ledger and
--      the auth.users row), then writes proof: per-table counts,
--      storage_objects_deleted, subject_hash (HMAC), proof_sha256, executed_by,
--      completed_at; subject_id is cleared. audit_log gets a hashed entry.
-- Nothing in the proof or the audit trail contains the raw id.

-- ---------------------------------------------------------------------------
-- Client RPC
-- ---------------------------------------------------------------------------
create or replace function public.request_deletion(p_scope public.deletion_scope, p_child_id uuid default null)
returns uuid
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_uid uuid := (select auth.uid());
  v_subject uuid;
  v_id uuid;
begin
  if v_uid is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if p_scope = 'child' then
    if p_child_id is null or not private.owns_child(p_child_id) then
      raise exception 'forbidden' using errcode = '42501';
    end if;
    v_subject := p_child_id;
  elsif p_scope = 'account' then
    if p_child_id is not null then
      raise exception 'child_id_not_allowed_for_account_scope' using errcode = '22023';
    end if;
    v_subject := v_uid;
  else
    raise exception 'invalid_scope' using errcode = '22023';
  end if;

  select d.id into v_id from public.deletion_request d
  where d.requested_by = v_uid and d.scope = p_scope and d.subject_id = v_subject
    and d.status in ('pending', 'processing');
  if found then
    return v_id;                                   -- idempotent
  end if;

  v_id := gen_random_uuid();
  insert into public.deletion_request (requested_by, scope, subject_id)
  values (v_uid, p_scope, v_subject);
  select d.id into v_id from public.deletion_request d
  where d.requested_by = v_uid and d.scope = p_scope and d.subject_id = v_subject
    and d.status = 'pending'
  order by d.requested_at desc limit 1;

  perform private.audit('deletion_requested', 'deletion_request', v_id,
                        jsonb_build_object('scope', p_scope::text));
  return v_id;
end
$$;

revoke all on function public.request_deletion(public.deletion_scope, uuid) from public, anon;
grant execute on function public.request_deletion(public.deletion_scope, uuid) to authenticated;

-- ---------------------------------------------------------------------------
-- Internals (owner privileges; never granted to clients)
-- ---------------------------------------------------------------------------

-- Children covered by a request.
create or replace function private.deletion_children(p_scope public.deletion_scope, p_subject uuid)
returns setof uuid
language sql
stable
security definer
set search_path = ''
as $$
  select p_subject where p_scope = 'child' and exists (select 1 from public.child_profile c where c.id = p_subject)
  union
  select c.id from public.child_profile c where p_scope = 'account' and c.owner_id = p_subject
$$;

-- Storage objects that must be removed (via Storage API) before completion.
create or replace function private.deletion_storage_paths(p_scope public.deletion_scope, p_subject uuid)
returns setof text
language sql
stable
security definer
set search_path = ''
as $$
  select o.name
  from storage.objects o
  where o.bucket_id = 'dataset_photos'
    and (
      split_part(o.name, '/', 2) in (select x::text from private.deletion_children(p_scope, p_subject) x)
      or (p_scope = 'account' and split_part(o.name, '/', 1) = p_subject::text)
    )
$$;

create or replace function private.count_child_rows(p_children uuid[])
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
  select jsonb_build_object(
    'child_profile',       (select count(*) from public.child_profile where id = any (p_children)),
    'consent',             (select count(*) from public.consent where child_id = any (p_children)),
    'screening',           (select count(*) from public.screening where child_id = any (p_children)),
    'referral',            (select count(*) from public.referral where child_id = any (p_children)),
    'brushing_event',      (select count(*) from public.brushing_event where child_id = any (p_children)),
    'image_upload',        (select count(*) from public.image_upload where child_id = any (p_children)),
    'class_roster_member', (select count(*) from public.class_roster_member where child_id = any (p_children)),
    'sync_event',          (select count(*) from public.sync_event where child_id = any (p_children))
  )
$$;

create or replace function private.execute_deletion(
  p_request_id uuid,
  p_executed_by text,
  p_storage_objects_deleted integer default 0
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  r public.deletion_request%rowtype;
  v_children uuid[];
  v_counts jsonb;
  v_hash text;
  v_now timestamptz := now();
  v_proof text;
  v_extra jsonb := '{}'::jsonb;
  v_n bigint;
begin
  select * into r from public.deletion_request d where d.id = p_request_id for update;
  if not found or r.status not in ('pending', 'processing') then
    return jsonb_build_object('status', 'skipped');
  end if;

  update public.deletion_request d
     set status = 'processing', started_at = coalesce(d.started_at, v_now), attempts = d.attempts + 1
   where d.id = r.id;

  if exists (select 1 from private.deletion_storage_paths(r.scope, r.subject_id)) then
    update public.deletion_request d
       set failure_code = 'awaiting_storage_purge',
           storage_objects_deleted = coalesce(d.storage_objects_deleted, 0) + greatest(coalesce(p_storage_objects_deleted, 0), 0)
     where d.id = r.id;
    return jsonb_build_object('status', 'awaiting_storage_purge');
  end if;

  select coalesce(array_agg(x), '{}'::uuid[]) into v_children
  from private.deletion_children(r.scope, r.subject_id) x;
  v_counts := private.count_child_rows(v_children);

  delete from public.child_profile c where c.id = any (v_children);   -- cascades

  if r.scope = 'account' then
    select count(*) into v_n from public.class_roster where kader_id = r.subject_id;
    v_extra := v_extra || jsonb_build_object('class_roster', v_n);
    delete from public.class_roster where kader_id = r.subject_id;

    select count(*) into v_n from public.sync_event where submitted_by = r.subject_id;
    v_extra := v_extra || jsonb_build_object('sync_event_account', v_n);
    delete from public.sync_event where submitted_by = r.subject_id;

    select count(*) into v_n from public.guardian where id = r.subject_id;
    v_extra := v_extra || jsonb_build_object('guardian', v_n);
    select count(*) into v_n from public.user_role where user_id = r.subject_id;
    v_extra := v_extra || jsonb_build_object('user_role', v_n);
    delete from public.guardian where id = r.subject_id;
    delete from public.user_role where user_id = r.subject_id;

    -- Other (older, completed) requests by this account lose the link.
    update public.deletion_request d set requested_by = null
     where d.requested_by = r.subject_id and d.id <> r.id;

    -- The auth account itself (cascades to auth.identities/sessions/etc.).
    select count(*) into v_n from auth.users where id = r.subject_id;
    v_extra := v_extra || jsonb_build_object('auth_user', v_n);
    delete from auth.users where id = r.subject_id;
  end if;

  v_counts := v_counts || v_extra;
  v_hash := private.hash_ref(r.subject_id);
  v_proof := r.id::text || '|' || r.scope::text || '|' || v_hash || '|' || v_counts::text || '|'
             || (coalesce(r.storage_objects_deleted, 0) + greatest(coalesce(p_storage_objects_deleted, 0), 0))::text
             || '|' || to_char(v_now at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"');

  update public.deletion_request d
     set status = 'completed',
         subject_id = null,
         requested_by = case when r.scope = 'account' then null else d.requested_by end,
         completed_at = v_now,
         executed_by = p_executed_by,
         subject_hash = v_hash,
         rows_deleted = v_counts,
         storage_objects_deleted = coalesce(d.storage_objects_deleted, 0) + greatest(coalesce(p_storage_objects_deleted, 0), 0),
         proof_sha256 = encode(extensions.digest(convert_to(v_proof, 'UTF8'), 'sha256'), 'hex'),
         failure_code = null
   where d.id = r.id;

  insert into public.audit_log (actor_role, actor_hash, action, target_table, target_hash, details)
  values (replace(p_executed_by, 'edge:', 'edge_'), null, 'deletion_completed', 'deletion_request', v_hash,
          jsonb_build_object('scope', r.scope::text, 'children', cardinality(v_children)));

  return jsonb_build_object('status', 'completed', 'rows_deleted', v_counts);
end
$$;

-- pg_cron entry point: complete what can be completed in SQL; never raises.
create or replace function private.run_pending_deletions(p_limit integer default 50)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_id uuid;
  v_done integer := 0;
  v_res jsonb;
  v_max integer := private.setting_int('deletion_max_attempts', 5);
begin
  for v_id in
    select d.id from public.deletion_request d
    where d.status in ('pending', 'processing')
    order by d.requested_at
    limit p_limit
  loop
    begin
      v_res := private.execute_deletion(v_id, 'pg_cron', 0);
      if v_res ->> 'status' = 'completed' then
        v_done := v_done + 1;
      end if;
    exception when others then
      update public.deletion_request d
         set failure_code = 'sqlstate_' || lower(sqlstate),
             attempts = d.attempts + 1,
             status = case when d.attempts + 1 >= v_max then 'failed'::public.deletion_status else d.status end
       where d.id = v_id;
    end;
  end loop;
  return v_done;
end
$$;

-- Retention for the idempotency ledger (images need the Storage API -> Edge).
create or replace function private.retention_sweep_db()
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_sync bigint;
begin
  delete from public.sync_event e
  where e.received_at < now() - make_interval(days => private.setting_int('sync_event_retention_days', 180));
  get diagnostics v_sync = row_count;
  insert into public.audit_log (actor_role, actor_hash, action, target_table, target_hash, details)
  values ('pg_cron', null, 'retention_sweep_db', 'sync_event', null, jsonb_build_object('sync_event_deleted', v_sync));
  return jsonb_build_object('sync_event_deleted', v_sync);
end
$$;

revoke all on function
  private.deletion_children(public.deletion_scope, uuid),
  private.deletion_storage_paths(public.deletion_scope, uuid),
  private.count_child_rows(uuid[]),
  private.execute_deletion(uuid, text, integer),
  private.run_pending_deletions(integer),
  private.retention_sweep_db()
from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- service_role RPCs for the Edge Functions (never granted to clients)
-- ---------------------------------------------------------------------------
create or replace function private.assert_service_role()
returns void
language plpgsql
stable
set search_path = ''
as $$
begin
  if coalesce(auth.role(), '') <> 'service_role' then
    raise exception 'forbidden' using errcode = '42501';
  end if;
end
$$;
revoke all on function private.assert_service_role() from public, anon, authenticated;
grant execute on function private.assert_service_role() to service_role;

create or replace function public.service_pending_deletions(p_limit integer default 20)
returns table (request_id uuid, storage_paths text[])
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
  perform private.assert_service_role();
  return query
    select d.id,
           coalesce((select array_agg(p) from private.deletion_storage_paths(d.scope, d.subject_id) p), '{}'::text[])
    from public.deletion_request d
    where d.status in ('pending', 'processing')
    order by d.requested_at
    limit least(greatest(p_limit, 1), 100);
end
$$;

create or replace function public.service_execute_deletion(p_request_id uuid, p_storage_objects_deleted integer)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
begin
  perform private.assert_service_role();
  return private.execute_deletion(p_request_id, 'edge:process-deletion', p_storage_objects_deleted);
end
$$;

-- Images whose TTL passed or whose consent (c) was withdrawn.
create or replace function public.service_retention_candidates(p_limit integer default 100)
returns table (upload_id uuid, object_path text)
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
  perform private.assert_service_role();
  return query
    select u.id, u.object_path
    from public.image_upload u
    where (u.status = 'uploaded' and u.expires_at <= now())
       or (u.status = 'withdrawn')
    order by u.expires_at
    limit least(greatest(p_limit, 1), 500);
end
$$;

create or replace function public.service_mark_images_deleted(p_upload_ids uuid[])
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_n integer;
begin
  perform private.assert_service_role();
  update public.image_upload u
     set status = 'deleted', deleted_at = now(),
         withdrawn_at = coalesce(u.withdrawn_at, case when u.status = 'withdrawn' then now() end)
   where u.id = any (p_upload_ids) and u.status in ('uploaded', 'withdrawn')
     and not exists (select 1 from storage.objects o
                     where o.bucket_id = 'dataset_photos' and o.name = u.object_path);
  get diagnostics v_n = row_count;
  insert into public.audit_log (actor_role, actor_hash, action, target_table, target_hash, details)
  values ('edge_retention_sweep', null, 'images_deleted', 'image_upload', null, jsonb_build_object('count', v_n));
  return v_n;
end
$$;

create or replace function public.service_retention_sweep_db()
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
begin
  perform private.assert_service_role();
  return private.retention_sweep_db();
end
$$;

revoke all on function
  public.service_pending_deletions(integer),
  public.service_execute_deletion(uuid, integer),
  public.service_retention_candidates(integer),
  public.service_mark_images_deleted(uuid[]),
  public.service_retention_sweep_db()
from public, anon, authenticated;

grant execute on function
  public.service_pending_deletions(integer),
  public.service_execute_deletion(uuid, integer),
  public.service_retention_candidates(integer),
  public.service_mark_images_deleted(uuid[]),
  public.service_retention_sweep_db()
to service_role;

-- ---------------------------------------------------------------------------
-- pg_cron (only if the extension is available — it is on Supabase; NOT on a
-- plain local Postgres). NOTE: pg_cron runs inside the database and cannot
-- wake a paused Free-plan project [PERLU VERIFIKASI whether internal activity
-- even counts]; keep the external keep-alive (ADR 008).
-- ---------------------------------------------------------------------------
do $$
begin
  if exists (select 1 from pg_catalog.pg_available_extensions where name = 'pg_cron') then
    begin
      execute 'create extension if not exists pg_cron';
      execute $cron$select cron.schedule('dentisai-process-deletions', '*/15 * * * *',
                                         'select private.run_pending_deletions()')$cron$;
      execute $cron$select cron.schedule('dentisai-retention-db', '17 3 * * *',
                                         'select private.retention_sweep_db()')$cron$;
    exception when others then
      raise notice 'DentisAI: pg_cron present but scheduling failed (sqlstate %); schedule manually, see supabase/README.md', sqlstate;
    end;
  else
    raise notice 'DentisAI: pg_cron not available; deletion/retention jobs not scheduled (expected on plain local Postgres)';
  end if;
end
$$;
