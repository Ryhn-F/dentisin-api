-- =============================================================================
-- DentisAI · 20261018000200 remove kader / class mode / facility aggregates;
-- teens register and watch their OWN brushing proof videos (ADR 018)
-- =============================================================================
-- Owner decision (2026-10-18): the app serves only guardian (orang tua), teen
-- (remaja, for themself) and child accounts (created by a guardian, ADR 017).
-- Admin stays for operations (not an app user).
--
-- Removed:
--   * role kader + class mode: tables class_roster / class_roster_member (with
--     their policies, indexes, grants), roster access in can_access_child, the
--     paper_via_kader consent method, the kader proof path of 20261018000100;
--   * public.facility_screening_summary (its only data source was the kader
--     roster) and every use of the facility_viewer role.
--   Postgres enum values cannot be dropped: app_role 'kader' / 'facility_viewer'
--   and consent_method 'paper_via_kader' stay in the types but are unusable:
--     - user_role rows holding them are deleted, CHECK user_role_role_available
--       refuses new ones, admin_grant_role answers 22023 role_not_available;
--     - paper_via_kader: sync consent_changed -> 'forbidden', policy
--       consent_insert accepts only 'in_app', trigger consent_method_guard
--       refuses it for every writer (also service_role / owner). Existing
--       historical consent rows are kept (withdrawal still works).
--   Children that a former kader owned stay owned by that account (now a plain
--   guardian-role account); nothing else references them.
--
-- Redefined (latest definition referenced class_roster / kader / facility_viewer):
--   private.can_access_child            owner only (== owns_child + not null)
--   private.current_actor_role          admin first, no retired roles
--   private.handle_user_updated         no class_roster check
--   private.provision_child_account     no kader refusal (no kader exists)
--   private.count_child_rows            no class_roster_member key
--   private.execute_deletion            no class_roster purge
--   private.sync_consent_changed        guardian role only; paper_via_kader -> forbidden
--   private.can_register_proof          child account (own child) OR teen (own self profile)
--   private.sync_brushing_proof_registered  comments only (uses can_register_proof)
--   public.admin_grant_role             refuses kader / facility_viewer
--   public.my_account                   capabilities without kader logic
--   public.child_history                owner branch simplified (no roster reader)
--   policies child_profile_insert, consent_insert
--
-- Teen proof path (ADR 018 §1.3): brushing_recorded (self profile) ->
-- brushing_proof_registered (object_path <teen_uid>/<self_child_id>/<proof>.ext,
-- uploaded_by = teen) -> Storage INSERT by the teen at that exact path
-- (private.proof_upload_allowed -> can_register_proof) -> list_brushing_proofs /
-- mark_proof_viewed / delete_brushing_proof / child_history kind brushing_proof /
-- Storage SELECT (private.proof_object_readable: owner, not a child account) all
-- work through ownership. Consents (b) and (d) of the self profile are granted
-- by the teen itself with consent_changed (teens hold the guardian role).
-- Guardians do not register proofs (they watch their children's); admins never
-- read proofs.
-- Re-runnable: every statement is guarded / create-or-replace; re-applying the
-- older migrations afterwards recreates the roster tables, and re-applying this
-- file drops them again.

-- -----------------------------------------------------------------------------
-- 1. Retired roles
-- -----------------------------------------------------------------------------
delete from public.user_role ur where ur.role in ('kader', 'facility_viewer');

alter table public.user_role drop constraint if exists user_role_role_available;
alter table public.user_role
  add constraint user_role_role_available check (role not in ('kader', 'facility_viewer'));

comment on type public.app_role is
  'guardian = orang tua or remaja (guardian.account_type); child = child account (server-provisioned, ADR 017); admin = audited operations. kader and facility_viewer are RETIRED (ADR 018): enum values cannot be dropped, no user_role row may hold them (user_role_role_available) and admin_grant_role refuses them.';
comment on table public.user_role is
  'Roles per account: guardian (orang tua / remaja), child (child account), admin. Not writable by clients. facility_code is legacy (no role uses it since ADR 018).';
comment on table public.guardian is
  'Account profile of a guardian (orang tua) or teen (ADR 017/018). Intentionally holds no identity data; child accounts have no row here.';
comment on table public.brushing_proof is
  'Brushing proof clip metadata (ADR 017 §2.5, ADR 018). Registered by the child account (its own child) or a teen (its own self profile); read by the owner of the child. Video only in bucket brushing_proofs; consent (d) + flag brushing_proof_video; 7-day retention; never for training; no access for admin.';

-- -----------------------------------------------------------------------------
-- 2. Retired consent method paper_via_kader
-- -----------------------------------------------------------------------------
comment on type public.consent_method is
  'in_app only. paper_via_kader is RETIRED (ADR 018): refused by sync consent_changed, policy consent_insert and trigger consent_method_guard; the value stays because enum values cannot be dropped (historical rows may hold it).';

create or replace function private.consent_method_guard()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if new.method is distinct from 'in_app'::public.consent_method then
    raise exception 'consent_method_not_available' using errcode = '22023';
  end if;
  return new;
end
$$;

revoke all on function private.consent_method_guard() from public, anon, authenticated, service_role;

drop trigger if exists consent_method_guard on public.consent;
create trigger consent_method_guard
  before insert on public.consent
  for each row execute function private.consent_method_guard();

drop policy if exists consent_insert on public.consent;
create policy consent_insert on public.consent
  for insert to authenticated
  with check (
    granted_by = (select auth.uid())
    and private.can_access_child(child_id)
    and method = 'in_app'
    and not private.deletion_pending(child_id)
  );

-- New children (empty shells) only by guardian-role accounts (guardian / teen).
drop policy if exists child_profile_insert on public.child_profile;
create policy child_profile_insert on public.child_profile
  for insert to authenticated
  with check (
    owner_id = (select auth.uid())
    and private.has_role('guardian')
    and birth_year is null and birth_month is null and cardinality(allergies) = 0
  );

-- -----------------------------------------------------------------------------
-- 3. Access helpers
-- -----------------------------------------------------------------------------

-- Owner (guardian / teen) of the child. NEVER true for a child account (child
-- access: private.child_can_access). Since ADR 018 identical to owns_child()
-- except that a NULL id is FALSE.
create or replace function private.can_access_child(p_child uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select p_child is not null
     and not private.is_child_account()
     and exists (select 1 from public.child_profile c
                 where c.id = p_child and c.owner_id = (select auth.uid()))
$$;

-- Highest app role of the caller, for audit_log.actor_role.
create or replace function private.current_actor_role()
returns text
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(
    (select ur.role::text from public.user_role ur
     where ur.user_id = (select auth.uid())
     order by case ur.role when 'admin' then 1 else 2 end, ur.role::text
     limit 1),
    nullif(auth.role(), ''),
    'system')
$$;

-- Who may register / upload a brushing proof for a child (ADR 018 §1.4):
--   (1) an enabled child account, for its OWN child;
--   (2) a teen account, for its OWN self profile (is_self).
-- Nobody else (not guardians, not admins). Never NULL.
create or replace function private.can_register_proof(p_child uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select p_child is not null
     and (
       private.child_can_access(p_child)
       or (private.is_teen()
           and exists (select 1 from public.child_profile c
                       where c.id = p_child and c.owner_id = (select auth.uid()) and c.is_self))
     )
$$;

revoke all on function private.can_register_proof(uuid) from public, anon;
grant execute on function private.can_register_proof(uuid) to authenticated, service_role;

-- -----------------------------------------------------------------------------
-- 4. Account provisioning / conversion
-- -----------------------------------------------------------------------------
create or replace function private.provision_child_account(p_user uuid, p_app jsonb)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_guardian uuid := private.try_uuid(p_app ->> 'guardian_id');
  v_child uuid := private.try_uuid(p_app ->> 'child_id');
  v_username text := p_app ->> 'username';
begin
  if v_guardian is null or v_child is null or v_username is null
     or jsonb_typeof(p_app -> 'username') <> 'string'
     or v_username !~ '^[a-z0-9][a-z0-9._-]{2,19}$'
     or v_guardian = p_user then
    raise exception 'child_account_invalid' using errcode = '22023';
  end if;
  -- The guardian must be a guardian-type account (not teen, not child) that owns
  -- the (non-self) child; nothing may be pending deletion.
  if not exists (select 1 from public.guardian g
                 where g.id = v_guardian and g.account_type = 'guardian'::public.account_type)
     or exists (select 1 from public.child_account ca where ca.user_id = v_guardian)
     or not exists (select 1 from public.child_profile c
                    where c.id = v_child and c.owner_id = v_guardian and not c.is_self)
     or exists (select 1 from public.deletion_request d
                where d.status in ('pending', 'processing')
                  and ((d.scope = 'child' and d.subject_id = v_child)
                       or (d.scope = 'account' and d.subject_id = v_guardian))) then
    raise exception 'child_account_invalid' using errcode = '22023';
  end if;
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('child_account:' || v_guardian::text, 0));
  if (select count(*) from public.child_account ca where ca.guardian_id = v_guardian)
     >= private.setting_int('max_child_accounts_per_guardian', 6) then
    raise exception 'child_account_limit' using errcode = '22023';
  end if;
  insert into public.child_account (user_id, child_id, guardian_id, username)
  values (p_user, v_child, v_guardian, v_username);
  insert into public.user_role (user_id, role) values (p_user, 'child'::public.app_role)
  on conflict (user_id, role) do nothing;
end
$$;

revoke all on function private.provision_child_account(uuid, jsonb) from public, anon, authenticated, service_role;

-- GoTrue's admin "create user" may write app_metadata with an UPDATE right after
-- the INSERT (same transaction). A freshly created, still empty account whose
-- app_metadata becomes account_type = 'child' is converted into a child account
-- (guardian row + guardian role removed). Anything else is refused (fail closed).
-- Also mirrors last_sign_in_at into child_account.last_login_at.
create or replace function private.handle_user_updated()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_new text := case when jsonb_typeof(new.raw_app_meta_data) = 'object' then new.raw_app_meta_data ->> 'account_type' end;
  v_old text := case when jsonb_typeof(old.raw_app_meta_data) = 'object' then old.raw_app_meta_data ->> 'account_type' end;
begin
  if v_new = 'child' and v_old is distinct from 'child'
     and not exists (select 1 from public.child_account ca where ca.user_id = new.id) then
    if coalesce(new.created_at, now()) < now() - interval '10 minutes'
       or exists (select 1 from public.child_profile c where c.owner_id = new.id)
       or exists (select 1 from public.user_role ur where ur.user_id = new.id and ur.role <> 'guardian')
       or exists (select 1 from public.consent c where c.granted_by = new.id)
       or exists (select 1 from public.sync_event e where e.submitted_by = new.id)
       or exists (select 1 from public.deletion_request d where d.requested_by = new.id) then
      raise exception 'child_account_conversion_refused' using errcode = '42501';
    end if;
    delete from public.user_role ur where ur.user_id = new.id and ur.role = 'guardian';
    delete from public.guardian g where g.id = new.id;
    perform private.provision_child_account(new.id, new.raw_app_meta_data);
  end if;
  if new.last_sign_in_at is distinct from old.last_sign_in_at then
    update public.child_account ca set last_login_at = new.last_sign_in_at where ca.user_id = new.id;
  end if;
  return new;
end
$$;

-- -----------------------------------------------------------------------------
-- 5. Admin: retired roles cannot be granted
-- -----------------------------------------------------------------------------
create or replace function public.admin_grant_role(p_user uuid, p_role public.app_role, p_facility_code text default null)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  if not private.is_admin() then
    raise exception 'forbidden' using errcode = '42501';
  end if;
  if p_role in ('kader'::public.app_role, 'facility_viewer'::public.app_role) then
    raise exception 'role_not_available' using errcode = '22023';      -- retired (ADR 018)
  end if;
  if p_user is null or not exists (select 1 from auth.users u where u.id = p_user) then
    raise exception 'unknown_user' using errcode = '22023';
  end if;
  if p_role = 'child'::public.app_role then
    raise exception 'role_not_grantable' using errcode = '22023';
  end if;
  if exists (select 1 from public.child_account ca where ca.user_id = p_user) then
    raise exception 'role_conflict_child_account' using errcode = '22023';
  end if;
  -- Separation of duties: admin accounts must not own child data.
  if p_role = 'admin'::public.app_role
     and exists (select 1 from public.child_profile c where c.owner_id = p_user) then
    raise exception 'role_conflict_owns_children' using errcode = '22023';
  end if;
  insert into public.user_role (user_id, role, facility_code, granted_by)
  values (p_user, p_role, p_facility_code, (select auth.uid()))
  on conflict (user_id, role) do update
    set facility_code = excluded.facility_code, granted_by = excluded.granted_by, granted_at = now();
  if p_role = 'admin'::public.app_role then
    delete from public.user_role ur where ur.user_id = p_user and ur.role = 'guardian';
  end if;
  perform private.audit('role_granted', 'user_role', p_user, jsonb_build_object('role', p_role::text));
end
$$;

-- -----------------------------------------------------------------------------
-- 6. sync_events handlers
-- -----------------------------------------------------------------------------
create or replace function private.sync_consent_changed(p_child uuid, p jsonb)
returns text
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_uid uuid := (select auth.uid());
  v_type public.consent_type;
  v_action text := p ->> 'action';
  v_at timestamptz;
  v_method public.consent_method;
begin
  if not private.jsonb_only_keys(p, array['child_id', 'consent_type', 'action', 'text_version', 'at', 'method'])
     or v_action is null or v_action not in ('grant', 'withdraw')
     or p ->> 'consent_type' is null then
    return 'schema_invalid';
  end if;
  v_type := (p ->> 'consent_type')::public.consent_type;
  v_at := coalesce(private.sane_ts(p ->> 'at'), now());
  v_method := coalesce(p ->> 'method', 'in_app')::public.consent_method;

  -- Only in-app consent exists (paper consent recorded by a third party is retired).
  if v_method <> 'in_app'::public.consent_method then
    return 'forbidden';
  end if;

  if not private.child_exists(p_child) then
    if v_action = 'withdraw' then
      return null;                       -- nothing to withdraw: no-op
    end if;
    if not private.has_role('guardian') then
      return 'forbidden';
    end if;
    insert into public.child_profile (id, owner_id) values (p_child, v_uid);   -- empty shell
  elsif not private.can_access_child(p_child) then
    return 'forbidden';
  end if;

  if v_action = 'grant' then
    -- Withdrawals are always accepted (more private); grants are not while a
    -- deletion request is pending.
    if private.deletion_pending(p_child) then
      return 'deletion_pending';
    end if;
    if p ->> 'text_version' is null then
      return 'schema_invalid';
    end if;
    if not private.has_active_consent(p_child, v_type) then
      insert into public.consent (child_id, granted_by, consent_type, text_version, method, granted_at)
      values (p_child, v_uid, v_type, p ->> 'text_version', v_method, v_at);
    end if;
  else
    update public.consent c
       set withdrawn_at = greatest(v_at, c.granted_at)
     where c.child_id = p_child and c.consent_type = v_type and c.withdrawn_at is null;
  end if;
  return null;
end
$$;

create or replace function private.sync_brushing_proof_registered(p_child uuid, p jsonb)
returns text
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_proof uuid := private.try_uuid(p ->> 'proof_id');
  v_event uuid := private.try_uuid(p ->> 'brushing_event_id');
  v_mime text := p ->> 'mime';
  v_size integer;
  v_duration integer;
  v_owner uuid;
begin
  if not private.jsonb_only_keys(p, array['proof_id', 'child_id', 'brushing_event_id', 'size_bytes',
                                          'duration_s', 'mime'])
     or v_proof is null or v_event is null
     or jsonb_typeof(p -> 'mime') is distinct from 'string'
     or v_mime not in ('video/mp4', 'video/quicktime')
     or jsonb_typeof(p -> 'size_bytes') is distinct from 'number' or (p ->> 'size_bytes') !~ '^[0-9]{1,9}$'
     or jsonb_typeof(p -> 'duration_s') is distinct from 'number' or (p ->> 'duration_s') !~ '^[0-9]{1,9}$' then
    return 'schema_invalid';
  end if;
  v_size := (p ->> 'size_bytes')::integer;
  v_duration := (p ->> 'duration_s')::integer;
  if v_size < 1 or v_duration < 1 then
    return 'schema_invalid';
  end if;
  if not private.can_register_proof(p_child) then
    return 'forbidden';                     -- the child's own account, or the teen for its self profile
  end if;
  if private.deletion_pending(p_child) then
    return 'deletion_pending';
  end if;
  if not private.flag_enabled('brushing_proof_video') then
    return 'feature_disabled';
  end if;
  if not private.has_active_consent(p_child, 'b') or not private.has_active_consent(p_child, 'd') then
    return 'consent_required';
  end if;
  if v_size > least(private.setting_int('proof_max_bytes', 10485760), 52428800)
     or v_duration > private.setting_int('proof_max_duration_s', 180) then
    return 'limit_exceeded';
  end if;
  if (select count(*) from public.brushing_proof bp
      where bp.child_id = p_child and bp.uploaded_at >= private.wib_day_start())
     >= private.setting_int('proof_max_per_child_per_day', 3) then
    return 'limit_exceeded';
  end if;
  if not exists (select 1 from public.brushing_event e where e.id = v_event and e.child_id = p_child) then
    return 'schema_invalid';                -- brushing_recorded must be applied first
  end if;
  select c.owner_id into v_owner from public.child_profile c where c.id = p_child;
  if v_owner is null then
    return 'forbidden';
  end if;
  -- Path prefix = owner of the child: the guardian (child account) or the teen itself.
  insert into public.brushing_proof (id, child_id, brushing_event_id, uploaded_by, object_path, mime,
                                     size_bytes, duration_s)
  values (v_proof, p_child, v_event, (select auth.uid()),
          v_owner::text || '/' || p_child::text || '/' || v_proof::text || '.'
            || case v_mime when 'video/mp4' then 'mp4' else 'mov' end,
          v_mime, v_size, v_duration);
  return null;
end
$$;

-- -----------------------------------------------------------------------------
-- 7. Right to delete (no roster any more)
-- -----------------------------------------------------------------------------
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

revoke all on function
  private.count_child_rows(uuid[]),
  private.execute_deletion(uuid, text, integer)
from public, anon, authenticated, service_role;

-- -----------------------------------------------------------------------------
-- 8. Client RPCs
-- -----------------------------------------------------------------------------

-- my_account(): + capabilities (what the UI may offer)
--   create_child_accounts  account_type = 'guardian'
--   register_proofs        child account -> its proof_allowed;
--                          teen -> flag brushing_proof_video on; others false
create or replace function public.my_account()
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_uid uuid := (select auth.uid());
  v_type text;
  v_roles text[];
  v_self uuid;
  v_child jsonb;
  v_count integer;
  v_ca record;
  v_proof boolean := false;
begin
  if v_uid is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  v_type := private.account_type();
  select coalesce(array_agg(ur.role::text order by ur.role::text), '{}'::text[]) into v_roles
  from public.user_role ur where ur.user_id = v_uid;

  if v_type = 'child' then
    select ca.child_id, ca.guardian_id, ca.username, ca.disabled_at is not null as disabled
      into v_ca from public.child_account ca where ca.user_id = v_uid;
    if found then
      v_proof := not v_ca.disabled
                 and private.flag_enabled('brushing_proof_video')
                 and private.has_active_consent(v_ca.child_id, 'b')
                 and private.has_active_consent(v_ca.child_id, 'd')
                 and not private.deletion_pending(v_ca.child_id);
      v_child := jsonb_build_object(
        'child_id', v_ca.child_id,
        'guardian_id', v_ca.guardian_id,
        'username', v_ca.username,
        'disabled', v_ca.disabled,
        'sync_allowed', not v_ca.disabled
                        and private.has_active_consent(v_ca.child_id, 'b')
                        and not private.deletion_pending(v_ca.child_id),
        'proof_allowed', v_proof);
    end if;
  else
    select c.id into v_self from public.child_profile c where c.owner_id = v_uid and c.is_self;
    select count(*)::integer into v_count from public.child_account ca where ca.guardian_id = v_uid;
    v_proof := v_type = 'teen' and private.flag_enabled('brushing_proof_video');
  end if;

  return jsonb_build_object(
    'user_id', v_uid,
    'account_type', v_type,
    'roles', to_jsonb(v_roles),
    'self_child_id', v_self,
    'child_account', v_child,
    'child_accounts', v_count,
    'flags', jsonb_build_object('brushing_proof_video', private.flag_enabled('brushing_proof_video')),
    'capabilities', jsonb_build_object(
      'create_child_accounts', coalesce(v_type = 'guardian', false),
      'register_proofs', coalesce(v_proof, false)),
    'limits', jsonb_build_object(
      'teen_min_age', private.setting_int('teen_min_age', 10),
      'teen_max_age', private.setting_int('teen_max_age', 19),
      'max_child_accounts_per_guardian', private.setting_int('max_child_accounts_per_guardian', 6),
      'streak_min_duration_s', private.setting_int('streak_min_duration_s', 60),
      'request_max_per_child_per_day', private.setting_int('request_max_per_child_per_day', 5),
      'request_expiry_days', private.setting_int('request_expiry_days', 14),
      'proof_max_bytes', private.setting_int('proof_max_bytes', 10485760),
      'proof_max_duration_s', private.setting_int('proof_max_duration_s', 180),
      'proof_max_per_child_per_day', private.setting_int('proof_max_per_child_per_day', 3),
      'proof_retention_days', private.setting_int('proof_retention_days', 7)));
end
$$;

comment on function public.my_account() is
  'Account facts for app routing (ADR 017/018): type, roles, child_account, flags, capabilities, limits.';

-- child_history(): merged timeline of one child.
-- kinds: screening | brushing | referral | screening_request | brushing_proof.
-- Owner (guardian / teen): all kinds. Child account (own child only): brushing,
-- screening_request, brushing_proof. Nobody else. `data` holds codes/numbers/ids
-- only: no PII, no username, no storage path/URL.
-- Result: {items:[{kind,id,occurred_at,data}], next_before}.
create or replace function public.child_history(
  p_child uuid,
  p_before timestamptz default null,
  p_limit integer default 30,
  p_kinds text[] default null,
  p_from timestamptz default null
)
returns jsonb
language plpgsql
stable
security invoker
set search_path = ''
as $$
declare
  v_all text[] := array['screening', 'brushing', 'referral', 'screening_request', 'brushing_proof'];
  v_kinds text[];
  v_limit integer := least(greatest(coalesce(p_limit, 30), 1), 100);
  v_min integer := private.setting_int('streak_min_duration_s', 60);
  v_items jsonb;
  v_more boolean;
  v_boundary timestamptz;
begin
  if (select auth.uid()) is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if p_kinds is not null
     and (cardinality(p_kinds) = 0
          or exists (select 1 from unnest(p_kinds) k where k is null or not (k = any (v_all)))) then
    raise exception 'invalid_kind' using errcode = '22023';
  end if;
  v_kinds := coalesce(p_kinds, v_all);

  if private.is_child_account() then
    if not private.child_can_access(p_child) then
      raise exception 'forbidden' using errcode = '42501';
    end if;
    v_kinds := array(select k from unnest(v_kinds) k where k in ('brushing', 'screening_request', 'brushing_proof'));
  elsif not private.owns_child(p_child) then
    raise exception 'forbidden' using errcode = '42501';
  end if;

  with items as (
    select 'screening'::text as kind, s.id, s.screened_at as occurred_at,
           jsonb_build_object('level', s.level, 'red_flag', s.red_flag,
                              'model_version', s.model_version, 'rules_version', s.rules_version,
                              'findings_count', jsonb_array_length(s.findings),
                              'request_id', s.request_id) as data
    from public.screening s
    where 'screening' = any (v_kinds) and s.child_id = p_child
      and (p_before is null or s.screened_at < p_before) and (p_from is null or s.screened_at >= p_from)
    union all
    select 'brushing', e.id, e.started_at,
           jsonb_build_object('duration_s', e.duration_s, 'spirit_score', e.spirit_score, 'slot', e.slot,
                              'camera_mode', e.camera_mode, 'counts_for_streak', e.duration_s >= v_min,
                              'has_proof', exists (select 1 from public.brushing_proof bp
                                                   where bp.brushing_event_id = e.id
                                                     and bp.status in ('uploaded', 'viewed')
                                                     and bp.expires_at > now() and bp.object_removed_at is null))
    from public.brushing_event e
    where 'brushing' = any (v_kinds) and e.child_id = p_child
      and (p_before is null or e.started_at < p_before) and (p_from is null or e.started_at >= p_from)
    union all
    select 'referral', f.id, f.referred_at,
           jsonb_build_object('level', f.level, 'reason_codes', to_jsonb(f.reason_codes),
                              'facility_code', f.facility_code, 'screening_id', f.screening_id)
    from public.referral f
    where 'referral' = any (v_kinds) and f.child_id = p_child
      and (p_before is null or f.referred_at < p_before) and (p_from is null or f.referred_at >= p_from)
    union all
    select 'screening_request', r.id, r.created_at,
           jsonb_build_object('reason_code', r.reason_code, 'pain_level', r.pain_level,
                              'status', case when r.status in ('pending', 'seen') and r.expires_at <= now()
                                             then 'expired' else r.status::text end,
                              'seen_at', r.seen_at, 'responded_at', r.responded_at,
                              'screening_id', r.screening_id, 'expires_at', r.expires_at)
    from public.screening_request r
    where 'screening_request' = any (v_kinds) and r.child_id = p_child
      and (p_before is null or r.created_at < p_before) and (p_from is null or r.created_at >= p_from)
    union all
    select 'brushing_proof', p.id, p.uploaded_at,
           jsonb_build_object('brushing_event_id', p.brushing_event_id, 'status', p.status,
                              'mime', p.mime, 'size_bytes', p.size_bytes, 'duration_s', p.duration_s,
                              'viewed_at', p.viewed_at, 'expires_at', p.expires_at)
    from public.brushing_proof p
    where 'brushing_proof' = any (v_kinds) and p.child_id = p_child
      and p.status in ('uploaded', 'viewed') and p.expires_at > now() and p.object_removed_at is null
      and private.has_active_consent(p.child_id, 'd')
      and (p_before is null or p.uploaded_at < p_before) and (p_from is null or p.uploaded_at >= p_from)
  ),
  b as (
    select i.occurred_at as boundary from items i order by i.occurred_at desc limit 1 offset (v_limit - 1)
  )
  select coalesce(jsonb_agg(jsonb_build_object('kind', i.kind, 'id', i.id, 'occurred_at', i.occurred_at,
                                               'data', i.data)
                            order by i.occurred_at desc, i.kind, i.id desc)
                    filter (where b.boundary is null or i.occurred_at >= b.boundary), '[]'::jsonb),
         coalesce(bool_or(b.boundary is not null and i.occurred_at < b.boundary), false),
         max(b.boundary)
    into v_items, v_more, v_boundary
  from items i
  left join b on true;

  return jsonb_build_object('items', v_items,
                            'next_before', case when v_more then v_boundary end);
end
$$;

comment on function public.list_children() is
  'Home-screen summary of the caller''s OWN children (guardian: its children; teen: its self profile). Child accounts: 42501 forbidden.';
comment on function public.child_history(uuid, timestamptz, integer, text[], timestamptz) is
  'Merged timeline of one child (ADR 017/018). Owner: all kinds; child account (own child): brushing, screening_request, brushing_proof.';

-- -----------------------------------------------------------------------------
-- 9. Drop facility aggregates and the class roster (after every function that
--    referenced them has been redefined above)
-- -----------------------------------------------------------------------------
drop function if exists public.facility_screening_summary(date, date);

drop table if exists public.class_roster_member;
drop table if exists public.class_roster;
