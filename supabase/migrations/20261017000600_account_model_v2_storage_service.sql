-- =============================================================================
-- DentisAI · 20261017000600 account model v2 (ADR 017) — part 6/6: Storage
-- bucket brushing_proofs, service_role RPCs (child-login, sweeper), and the
-- right-to-delete / retention extensions
-- =============================================================================
-- Storage (mirrors dataset_photos, 0600):
--   brushing_proofs  PRIVATE, <= 10 MiB, video/mp4 + video/quicktime.
--     INSERT: only an enabled child account, only to the exact path of a
--             brushing_proof row it registered (status uploaded, not expired,
--             object not removed), flag brushing_proof_video ON, consent (d)
--             active, no pending deletion; metadata (when present) must match.
--     SELECT: only the owning guardian/teen (first path segment = auth.uid()),
--             only for live proofs (createSignedUrl needs this).
--     No UPDATE/DELETE for any client; no admin read; removal only by the
--     Edge Functions (service_role) through the Storage API.
--
-- service_role RPCs (contract for the Edge Functions; all assert service_role):
--   service_resolve_child_login(p_parent_email, p_username)
--       -> table(child_user_id uuid, auth_email text, disabled boolean); 0 rows when unknown
--   service_child_login_check(p_key_hash, p_max default null, p_window_s default null) -> boolean (true = allowed)
--   service_child_login_record(p_key_hash, p_ok, p_window_s default null) -> void
--   service_expired_proofs(p_limit default 100) -> table(proof_id uuid, object_path text)
--   service_mark_proof_object_removed(p_proof) -> void
--   service_child_users_of(p_guardian) -> table(child_id uuid, child_user_id uuid)
--   service_deletion_targets(p_request_id) -> table(proof_paths text[], child_user_ids uuid[])
--       (service_pending_deletions itself is unchanged)
-- Deletion: a request completes only when NO object of the subject remains in
-- dataset_photos AND brushing_proofs; completion also deletes the child Auth
-- users (child_account) of the covered children / of the deleted guardian and
-- purges their GoTrue audit entries. retention_sweep_db additionally expires
-- requests and proofs, purges proof rows whose object was removed, and old
-- child_login_attempt rows. Re-runnable.

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('brushing_proofs', 'brushing_proofs', false, 10485760, array['video/mp4', 'video/quicktime'])
on conflict (id) do update
  set public = excluded.public,
      file_size_limit = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;

-- -----------------------------------------------------------------------------
-- Storage policy predicates (definer: read brushing_proof regardless of RLS)
-- -----------------------------------------------------------------------------
create or replace function private.proof_upload_allowed(p_name text, p_metadata jsonb)
returns boolean
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_child uuid;
  v_mime text;
begin
  if (select auth.uid()) is null or p_name is null then
    return false;
  end if;
  v_child := private.own_child_id();                      -- NULL unless an enabled child account
  if v_child is null or split_part(p_name, '/', 2) <> v_child::text then
    return false;
  end if;
  select p.mime into v_mime
  from public.brushing_proof p
  where p.object_path = p_name and p.child_id = v_child and p.uploaded_by = (select auth.uid())
    and p.status = 'uploaded' and p.expires_at > now() and p.object_removed_at is null;
  if not found then
    return false;
  end if;
  if not private.flag_enabled('brushing_proof_video')
     or not private.has_active_consent(v_child, 'd')
     or private.deletion_pending(v_child) then
    return false;
  end if;
  if p_metadata is not null then
    if p_metadata ? 'size' and (p_metadata ->> 'size') ~ '^[0-9]+$'
       and (p_metadata ->> 'size')::bigint > least(private.setting_int('proof_max_bytes', 10485760), 10485760) then
      return false;
    end if;
    if p_metadata ? 'mimetype' and p_metadata ->> 'mimetype' is distinct from v_mime then
      return false;
    end if;
  end if;
  return true;
end
$$;

create or replace function private.proof_object_readable(p_name text)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select (select auth.uid()) is not null
     and p_name is not null
     and not private.is_child_account()
     and split_part(p_name, '/', 1) = (select auth.uid())::text
     and exists (
       select 1 from public.brushing_proof p
       join public.child_profile c on c.id = p.child_id
       where p.object_path = p_name and c.owner_id = (select auth.uid())
         and p.status in ('uploaded', 'viewed') and p.expires_at > now() and p.object_removed_at is null
         and private.has_active_consent(p.child_id, 'd'))
$$;

revoke all on function private.proof_upload_allowed(text, jsonb), private.proof_object_readable(text) from public, anon;
grant execute on function private.proof_upload_allowed(text, jsonb), private.proof_object_readable(text)
  to authenticated, service_role;

drop policy if exists dentisai_brushing_proofs_insert on storage.objects;
create policy dentisai_brushing_proofs_insert on storage.objects
  for insert to authenticated
  with check (bucket_id = 'brushing_proofs' and private.proof_upload_allowed(name, metadata));

drop policy if exists dentisai_brushing_proofs_select on storage.objects;
create policy dentisai_brushing_proofs_select on storage.objects
  for select to authenticated
  using (bucket_id = 'brushing_proofs' and private.proof_object_readable(name));
-- Intentionally NO update/delete policies for brushing_proofs.

-- -----------------------------------------------------------------------------
-- Child login (Edge Function child-login)
-- -----------------------------------------------------------------------------

-- (parent e-mail, username) -> the child's Auth user + synthetic e-mail.
-- disabled = child_account.disabled_at set, Auth ban active, or a deletion of
-- the child / the guardian account is pending. Zero rows when unknown (the Edge
-- Function must answer 401 invalid_credentials uniformly in every failure case).
create or replace function public.service_resolve_child_login(p_parent_email text, p_username text)
returns table (child_user_id uuid, auth_email text, disabled boolean)
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
  perform private.assert_service_role();
  if p_parent_email is null or p_username is null
     or length(p_parent_email) > 320 or length(p_username) > 64 then
    return;
  end if;
  return query
    select ca.user_id,
           cu.email::text,
           (ca.disabled_at is not null
            or coalesce(cu.banned_until > now(), false)
            or exists (select 1 from public.deletion_request d
                       where d.status in ('pending', 'processing')
                         and ((d.scope = 'child' and d.subject_id = ca.child_id)
                              or (d.scope = 'account' and d.subject_id = ca.guardian_id))))
    from auth.users g
    join public.guardian gp on gp.id = g.id and gp.account_type = 'guardian'::public.account_type
    join public.child_account ca on ca.guardian_id = g.id
    join auth.users cu on cu.id = ca.user_id
    where lower(g.email) = lower(btrim(p_parent_email))
      and ca.username = lower(btrim(p_username))
    limit 1;
end
$$;

-- Throttle: sliding window of `window_s` seconds made of 15 buckets.
create or replace function private.child_login_params(p_key_hash text, p_max integer, p_window_s integer,
                                                      out v_max integer, out v_window integer, out v_bucket integer)
language plpgsql
stable
set search_path = ''
as $$
begin
  if p_key_hash is null or p_key_hash !~ '^[0-9A-Za-z_+/=-]{32,128}$' then
    raise exception 'invalid_key' using errcode = '22023';
  end if;
  v_max := coalesce(p_max, private.setting_int('child_login_max_attempts', 8));
  v_window := coalesce(p_window_s, private.setting_int('child_login_window_s', 900));
  if v_max not between 1 and 1000 or v_window not between 60 and 86400 then
    raise exception 'invalid_argument' using errcode = '22023';
  end if;
  v_bucket := greatest(v_window / 15, 1);
end
$$;

create or replace function public.service_child_login_check(p_key_hash text, p_max integer default null,
                                                            p_window_s integer default null)
returns boolean
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  prm record;
  v_attempts bigint;
begin
  perform private.assert_service_role();
  select * into prm from private.child_login_params(p_key_hash, p_max, p_window_s);
  select coalesce(sum(a.attempts), 0) into v_attempts
  from public.child_login_attempt a
  where a.key_hash = p_key_hash and a.window_start > now() - make_interval(secs => prm.v_window);
  return v_attempts < prm.v_max;
end
$$;

-- p_ok = false -> +1 failed attempt in the current bucket; p_ok = true -> clear.
create or replace function public.service_child_login_record(p_key_hash text, p_ok boolean,
                                                             p_window_s integer default null)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  prm record;
  v_start timestamptz;
begin
  perform private.assert_service_role();
  select * into prm from private.child_login_params(p_key_hash, null, p_window_s);
  if coalesce(p_ok, false) then
    delete from public.child_login_attempt a where a.key_hash = p_key_hash;
    return;
  end if;
  v_start := to_timestamp(floor(extract(epoch from now()) / prm.v_bucket) * prm.v_bucket);
  insert into public.child_login_attempt (key_hash, window_start, attempts)
  values (p_key_hash, v_start, 1)
  on conflict (key_hash, window_start) do update set attempts = public.child_login_attempt.attempts + 1;
  delete from public.child_login_attempt a
   where a.key_hash = p_key_hash and a.window_start <= now() - make_interval(secs => prm.v_window);
end
$$;

-- -----------------------------------------------------------------------------
-- Proof sweeper (retention-sweep, to be extended by the lead)
-- Rows: object not yet removed AND (expired OR deleted/expired status OR consent
-- (d) no longer active). Plus orphan objects in the bucket without a row (e.g.
-- the child was deleted) older than 1 hour, returned with proof_id = NULL.
-- Edge usage: storage.from('brushing_proofs').remove(paths), then
-- service_mark_proof_object_removed(proof_id) for every non-null proof_id.
-- -----------------------------------------------------------------------------
create or replace function public.service_expired_proofs(p_limit integer default 100)
returns table (proof_id uuid, object_path text)
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_limit integer := least(greatest(coalesce(p_limit, 100), 1), 500);
begin
  perform private.assert_service_role();
  return query
    (select p.id, p.object_path
     from public.brushing_proof p
     where p.object_removed_at is null
       and (p.expires_at <= now()
            or p.status in ('deleted', 'expired')
            or not private.has_active_consent(p.child_id, 'd'))
     order by p.expires_at
     limit v_limit)
    union all
    (select null::uuid, o.name
     from storage.objects o
     where o.bucket_id = 'brushing_proofs'
       and o.created_at < now() - interval '1 hour'
       and not exists (select 1 from public.brushing_proof p where p.object_path = o.name)
     order by o.created_at
     limit v_limit);
end
$$;

-- Records that the Storage object is gone (no-op while it still exists, so a
-- failed Storage removal is retried by the next sweep). Live statuses become
-- 'expired'. The row itself is purged by retention_sweep_db.
create or replace function public.service_mark_proof_object_removed(p_proof uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  perform private.assert_service_role();
  update public.brushing_proof p
     set object_removed_at = now(),
         status = case when p.status in ('uploaded', 'viewed') then 'expired'::public.proof_status else p.status end
   where p.id = p_proof and p.object_removed_at is null
     and not exists (select 1 from storage.objects o
                     where o.bucket_id = 'brushing_proofs' and o.name = p.object_path);
end
$$;

-- Child Auth users of a guardian (account deletion / admin tooling).
create or replace function public.service_child_users_of(p_guardian uuid)
returns table (child_id uuid, child_user_id uuid)
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
  perform private.assert_service_role();
  return query
    select ca.child_id, ca.user_id from public.child_account ca
    where ca.guardian_id = p_guardian
    order by ca.created_at, ca.user_id;
end
$$;

-- -----------------------------------------------------------------------------
-- Right-to-delete extensions
-- -----------------------------------------------------------------------------

-- brushing_proofs objects covered by a request (path <guardian>/<child>/<proof>).
create or replace function private.deletion_proof_paths(p_scope public.deletion_scope, p_subject uuid)
returns setof text
language sql
stable
security definer
set search_path = ''
as $$
  select o.name
  from storage.objects o
  where o.bucket_id = 'brushing_proofs'
    and (
      split_part(o.name, '/', 2) in (select x::text from private.deletion_children(p_scope, p_subject) x)
      or (p_scope = 'account' and split_part(o.name, '/', 1) = p_subject::text)
    )
$$;

-- Child Auth users that go with a request: accounts of the covered children,
-- and (account scope) every child account created by that guardian.
create or replace function private.deletion_child_users(p_scope public.deletion_scope, p_subject uuid)
returns setof uuid
language sql
stable
security definer
set search_path = ''
as $$
  select ca.user_id from public.child_account ca
  where ca.child_id in (select x from private.deletion_children(p_scope, p_subject) x)
  union
  select ca.user_id from public.child_account ca
  where p_scope = 'account' and ca.guardian_id = p_subject
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
    'sync_event',          (select count(*) from public.sync_event where child_id = any (p_children)),
    'screening_request',   (select count(*) from public.screening_request where child_id = any (p_children)),
    'brushing_proof',      (select count(*) from public.brushing_proof where child_id = any (p_children)),
    'child_account',       (select count(*) from public.child_account where child_id = any (p_children))
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
  v_child_users uuid[];
  v_counts jsonb;
  v_hash text;
  v_now timestamptz := now();
  v_proof text;
  v_extra jsonb := '{}'::jsonb;
  v_n bigint;
  v_audit bigint := 0;
  v_email text;
begin
  select * into r from public.deletion_request d where d.id = p_request_id for update;
  if not found or r.status not in ('pending', 'processing') then
    return jsonb_build_object('status', 'skipped');
  end if;

  update public.deletion_request d
     set status = 'processing', started_at = coalesce(d.started_at, v_now), attempts = d.attempts + 1
   where d.id = r.id;

  if exists (select 1 from private.deletion_storage_paths(r.scope, r.subject_id))
     or exists (select 1 from private.deletion_proof_paths(r.scope, r.subject_id)) then
    update public.deletion_request d
       set failure_code = 'awaiting_storage_purge',
           storage_objects_deleted = coalesce(d.storage_objects_deleted, 0) + greatest(coalesce(p_storage_objects_deleted, 0), 0)
     where d.id = r.id;
    return jsonb_build_object('status', 'awaiting_storage_purge');
  end if;

  select coalesce(array_agg(x), '{}'::uuid[]) into v_children
  from private.deletion_children(r.scope, r.subject_id) x;
  -- Collect child Auth users BEFORE the cascade removes child_account rows.
  select coalesce(array_agg(x), '{}'::uuid[]) into v_child_users
  from private.deletion_child_users(r.scope, r.subject_id) x;
  v_counts := private.count_child_rows(v_children);

  delete from public.child_profile c where c.id = any (v_children);   -- cascades

  -- Child accounts (synthetic e-mail contains the child id) + their GoTrue trail.
  delete from auth.users u where u.id = any (v_child_users);
  get diagnostics v_n = row_count;
  v_extra := v_extra || jsonb_build_object('child_auth_user', v_n);
  if cardinality(v_child_users) > 0 or cardinality(v_children) > 0 then
    delete from auth.audit_log_entries a
     where exists (select 1 from unnest(v_child_users) x where strpos(a.payload::text, x::text) > 0)
        or exists (select 1 from unnest(v_children) x where strpos(a.payload::text, x::text) > 0);
    get diagnostics v_audit = row_count;
  end if;

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
    select lower(u.email) into v_email from auth.users u where u.id = r.subject_id;
    select count(*) into v_n from auth.users where id = r.subject_id;
    v_extra := v_extra || jsonb_build_object('auth_user', v_n);
    delete from auth.users where id = r.subject_id;

    -- GoTrue's audit trail (no FK to auth.users): id / exact e-mail in the payload.
    delete from auth.audit_log_entries a
     where strpos(a.payload::text, r.subject_id::text) > 0
        or (v_email is not null and strpos(lower(a.payload::text), '"' || v_email || '"') > 0);
    get diagnostics v_n = row_count;
    v_audit := v_audit + v_n;
  end if;
  v_extra := v_extra || jsonb_build_object('auth_audit_log_entries', v_audit);

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
          jsonb_build_object('scope', r.scope::text, 'children', cardinality(v_children),
                             'child_accounts', cardinality(v_child_users)));

  return jsonb_build_object('status', 'completed', 'rows_deleted', v_counts);
end
$$;

-- service_pending_deletions(p_limit) is UNCHANGED (request_id, storage_paths =
-- dataset_photos objects). The extra purge targets of one pending request come
-- from this function (kept separate so 0700 stays re-runnable and the existing
-- Edge contract does not change):
--   proof_paths    = brushing_proofs objects to remove via the Storage API
--   child_user_ids = child Auth users that completion deletes in SQL (the Edge
--                    Function may delete them first via auth.admin.deleteUser)
-- Zero rows when the request is not pending/processing.
create or replace function public.service_deletion_targets(p_request_id uuid)
returns table (proof_paths text[], child_user_ids uuid[])
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
  perform private.assert_service_role();
  return query
    select coalesce((select array_agg(p order by p) from private.deletion_proof_paths(d.scope, d.subject_id) p), '{}'::text[]),
           coalesce((select array_agg(u order by u) from private.deletion_child_users(d.scope, d.subject_id) u), '{}'::uuid[])
    from public.deletion_request d
    where d.id = p_request_id and d.status in ('pending', 'processing');
end
$$;

-- Daily DB retention (pg_cron dentisai-retention-db + service_retention_sweep_db).
create or replace function private.retention_sweep_db()
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_sync bigint;
  v_req bigint;
  v_proof_exp bigint;
  v_proof_rows bigint;
  v_login bigint;
begin
  delete from public.sync_event e
  where e.received_at < now() - make_interval(days => private.setting_int('sync_event_retention_days', 180));
  get diagnostics v_sync = row_count;

  update public.screening_request r set status = 'expired'
  where r.status in ('pending', 'seen') and r.expires_at <= now();
  get diagnostics v_req = row_count;

  update public.brushing_proof p set status = 'expired'
  where p.status in ('uploaded', 'viewed') and p.expires_at <= now();
  get diagnostics v_proof_exp = row_count;

  delete from public.brushing_proof p where p.object_removed_at is not null;
  get diagnostics v_proof_rows = row_count;

  delete from public.child_login_attempt a where a.window_start < now() - interval '1 day';
  get diagnostics v_login = row_count;

  insert into public.audit_log (actor_role, actor_hash, action, target_table, target_hash, details)
  values ('pg_cron', null, 'retention_sweep_db', 'sync_event', null,
          jsonb_build_object('sync_event_deleted', v_sync, 'screening_request_expired', v_req,
                             'brushing_proof_expired', v_proof_exp, 'brushing_proof_rows_deleted', v_proof_rows,
                             'child_login_attempt_deleted', v_login));
  return jsonb_build_object('sync_event_deleted', v_sync, 'screening_request_expired', v_req,
                            'brushing_proof_expired', v_proof_exp, 'brushing_proof_rows_deleted', v_proof_rows,
                            'child_login_attempt_deleted', v_login);
end
$$;

-- -----------------------------------------------------------------------------
-- Grants
-- -----------------------------------------------------------------------------
revoke all on function
  private.child_login_params(text, integer, integer),
  private.deletion_proof_paths(public.deletion_scope, uuid),
  private.deletion_child_users(public.deletion_scope, uuid),
  private.count_child_rows(uuid[]),
  private.execute_deletion(uuid, text, integer),
  private.retention_sweep_db()
from public, anon, authenticated, service_role;

revoke all on function
  public.service_resolve_child_login(text, text),
  public.service_child_login_check(text, integer, integer),
  public.service_child_login_record(text, boolean, integer),
  public.service_expired_proofs(integer),
  public.service_mark_proof_object_removed(uuid),
  public.service_child_users_of(uuid),
  public.service_deletion_targets(uuid)
from public, anon, authenticated;

grant execute on function
  public.service_resolve_child_login(text, text),
  public.service_child_login_check(text, integer, integer),
  public.service_child_login_record(text, boolean, integer),
  public.service_expired_proofs(integer),
  public.service_mark_proof_object_removed(uuid),
  public.service_child_users_of(uuid),
  public.service_deletion_targets(uuid)
to service_role;
