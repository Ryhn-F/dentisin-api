-- =============================================================================
-- DentisAI · 20261017000300 account model v2 (ADR 017) — part 3/6: helpers,
-- signup trigger, guard triggers, RLS policies
-- =============================================================================
-- Access matrix (ADR 017 §2.3; RLS is the source of truth):
--   guardian / teen  own children (teen: only the self profile) as before, plus
--                    read screening_request / brushing_proof / child_account of
--                    own children (status changes via RPC, part 5).
--   child account    read ITS child_profile row; insert/read brushing_event and
--                    screening_request of its child; read live brushing_proof
--                    metadata of its child; read its own child_account row.
--                    Nothing else. disabled_at set -> nothing at all.
-- private.can_access_child / private.owns_child are now explicitly FALSE for
-- child accounts, so every pre-existing policy (consent, screening, referral,
-- image_upload, rosters, storage dataset_photos, deletion requests ...) excludes
-- them. Child access goes only through private.child_can_access().
-- All SECURITY DEFINER functions: search_path = '', schema-qualified names, no
-- dynamic SQL. Re-runnable.

-- -----------------------------------------------------------------------------
-- Caller helpers
-- -----------------------------------------------------------------------------

-- Is the caller a child account (enabled or disabled)? Either marker counts.
create or replace function private.is_child_account()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (select 1 from public.child_account ca where ca.user_id = (select auth.uid()))
      or exists (select 1 from public.user_role ur
                 where ur.user_id = (select auth.uid()) and ur.role = 'child'::public.app_role)
$$;

-- Child account row exists and is not disabled.
create or replace function private.child_account_active()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (select 1 from public.child_account ca
                 where ca.user_id = (select auth.uid()) and ca.disabled_at is null)
$$;

-- The one child profile of an ACTIVE child account; NULL otherwise.
create or replace function private.own_child_id()
returns uuid
language sql
stable
security definer
set search_path = ''
as $$
  select ca.child_id from public.child_account ca
  where ca.user_id = (select auth.uid()) and ca.disabled_at is null
$$;

-- Child-account access to a child id (only its own child, only while active).
-- Never NULL (callers negate it).
create or replace function private.child_can_access(p_child uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(p_child is not null and p_child = private.own_child_id(), false)
$$;

-- 'guardian' | 'teen' | 'child' | NULL (no profile, e.g. legacy/system).
create or replace function private.account_type()
returns text
language sql
stable
security definer
set search_path = ''
as $$
  select case when private.is_child_account() then 'child'
              else (select g.account_type::text from public.guardian g where g.id = (select auth.uid())) end
$$;

create or replace function private.is_teen()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select not private.is_child_account()
     and exists (select 1 from public.guardian g
                 where g.id = (select auth.uid()) and g.account_type = 'teen'::public.account_type)
$$;

-- Start of the current calendar day in WIB (daily limits).
create or replace function private.wib_day_start()
returns timestamptz
language sql
stable
set search_path = ''
as $$
  select (date_trunc('day', now() at time zone 'Asia/Jakarta')) at time zone 'Asia/Jakarta'
$$;

-- Teen age window from year (+ optional month) of birth, in WIB. Strict when the
-- month is unknown: every possible age must lie inside [teen_min_age, teen_max_age].
-- With a month the birthday is assumed at the start of that month.
create or replace function private.teen_age_ok(p_year smallint, p_month smallint)
returns boolean
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_today date := (now() at time zone 'Asia/Jakarta')::date;
  v_min integer := private.setting_int('teen_min_age', 10);
  v_max integer := private.setting_int('teen_max_age', 19);
  v_lo integer;
  v_hi integer;
  v_months integer;
begin
  if p_year is null or (p_month is not null and p_month not between 1 and 12) then
    return false;
  end if;
  if p_month is null then
    v_hi := extract(year from v_today)::integer - p_year;
    v_lo := v_hi - 1;
  else
    v_months := (extract(year from v_today)::integer * 12 + extract(month from v_today)::integer)
                - (p_year * 12 + p_month);
    if v_months < 0 then
      return false;
    end if;
    v_lo := v_months / 12;
    v_hi := v_lo;
  end if;
  return v_lo >= v_min and v_hi <= v_max;
end
$$;

-- -----------------------------------------------------------------------------
-- Existing helpers made explicit for child accounts
-- -----------------------------------------------------------------------------

-- Owner of the child, or a kader whose own roster contains the child.
-- NEVER true for a child account (child access: private.child_can_access).
create or replace function private.can_access_child(p_child uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select p_child is not null
     and not private.is_child_account()
     and (
       exists (select 1 from public.child_profile c
               where c.id = p_child and c.owner_id = (select auth.uid()))
       or (
         private.has_role('kader'::public.app_role)
         and exists (select 1
                     from public.class_roster_member m
                     join public.class_roster r on r.id = m.roster_id
                     where m.child_id = p_child and r.kader_id = (select auth.uid()))
       )
     )
$$;

-- Owner (guardian/teen/kader-owner) of the child. NEVER true for a child account.
create or replace function private.owns_child(p_child uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select not private.is_child_account()
     and exists (select 1 from public.child_profile c
                 where c.id = p_child and c.owner_id = (select auth.uid()))
$$;

-- A pending/processing deletion request blocks further sync for the subject:
-- the child itself, the caller's account, or (new) the account that OWNS the
-- child, so a child account stops syncing when its guardian deletes the account.
create or replace function private.deletion_pending(p_child uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.deletion_request d
    where d.status in ('pending', 'processing')
      and ((d.scope = 'child' and d.subject_id = p_child)
           or (d.scope = 'account' and d.subject_id = (select auth.uid()))
           or (d.scope = 'account'
               and d.subject_id = (select c.owner_id from public.child_profile c where c.id = p_child)))
  )
$$;

-- -----------------------------------------------------------------------------
-- Child account provisioning (signup trigger). Input = raw_app_meta_data, which
-- only the server (service_role / GoTrue admin API) can write.
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

-- -----------------------------------------------------------------------------
-- Signup trigger (replaces the "always guardian" version of 0300):
--   raw_app_meta_data.account_type = 'child' (server-controlled) -> child_account
--     + role child; NO guardian row, NO guardian role.
--   otherwise raw_user_meta_data.account_type (client-controlled, untrusted):
--     only 'teen' or 'guardian' are honoured, anything else -> guardian. Both get
--     the guardian row (with account_type) and ONLY the 'guardian' role.
-- Roles are never taken from metadata.
-- -----------------------------------------------------------------------------
create or replace function private.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_app jsonb := case when jsonb_typeof(new.raw_app_meta_data) = 'object' then new.raw_app_meta_data else '{}'::jsonb end;
  v_meta jsonb := case when jsonb_typeof(new.raw_user_meta_data) = 'object' then new.raw_user_meta_data else '{}'::jsonb end;
  v_type public.account_type;
begin
  if v_app ->> 'account_type' = 'child' then
    perform private.provision_child_account(new.id, v_app);
    return new;
  end if;
  v_type := case when v_meta ->> 'account_type' = 'teen' then 'teen' else 'guardian' end::public.account_type;
  insert into public.guardian (id, account_type) values (new.id, v_type) on conflict (id) do nothing;
  insert into public.user_role (user_id, role) values (new.id, 'guardian')
  on conflict (user_id, role) do nothing;
  return new;
end
$$;

drop trigger if exists dentisai_on_auth_user_created on auth.users;
create trigger dentisai_on_auth_user_created
  after insert on auth.users
  for each row execute function private.handle_new_user();

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
       or exists (select 1 from public.class_roster r where r.kader_id = new.id)
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

drop trigger if exists dentisai_on_auth_user_updated on auth.users;
create trigger dentisai_on_auth_user_updated
  after update of raw_app_meta_data, last_sign_in_at on auth.users
  for each row execute function private.handle_user_updated();

-- -----------------------------------------------------------------------------
-- child_profile: teen / self-profile guard
--   * a child account never owns profiles;
--   * is_self = true only for teen accounts, immutable, birth_year required and
--     inside the teen age window whenever birth data is set/changed;
--   * a teen owns ONLY its self profile (unique partial index = at most one).
-- -----------------------------------------------------------------------------
create or replace function private.child_profile_account_guard()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_type public.account_type;
begin
  if tg_op = 'UPDATE' and new.is_self is distinct from old.is_self then
    raise exception 'is_self_immutable' using errcode = '42501';
  end if;
  if exists (select 1 from public.child_account ca where ca.user_id = new.owner_id) then
    raise exception 'child_account_cannot_own_profiles' using errcode = '42501';
  end if;
  select g.account_type into v_type from public.guardian g where g.id = new.owner_id;
  if new.is_self then
    if v_type is distinct from 'teen'::public.account_type then
      raise exception 'self_profile_requires_teen' using errcode = '42501';
    end if;
    if new.birth_year is null then
      raise exception 'self_profile_requires_birth_year' using errcode = '23514';
    end if;
    if (tg_op = 'INSERT' or new.birth_year is distinct from old.birth_year
        or new.birth_month is distinct from old.birth_month)
       and not private.teen_age_ok(new.birth_year, new.birth_month) then
      raise exception 'teen_age_out_of_range' using errcode = '23514';
    end if;
  elsif v_type = 'teen'::public.account_type then
    raise exception 'teen_owns_only_self_profile' using errcode = '42501';
  end if;
  return new;
end
$$;

drop trigger if exists child_profile_account_guard on public.child_profile;
create trigger child_profile_account_guard
  before insert or update on public.child_profile
  for each row execute function private.child_profile_account_guard();

-- -----------------------------------------------------------------------------
-- screening_request: server-controlled columns + daily limit (also for direct
-- PostgREST inserts). Limit violation -> SQLSTATE DA429 ('limit_exceeded').
-- -----------------------------------------------------------------------------
create or replace function private.screening_request_before_insert()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('screening_request:' || new.child_id::text, 0));
  new.status := 'pending';
  new.created_at := now();
  new.seen_at := null;
  new.responded_at := null;
  new.screening_id := null;
  new.expires_at := now() + make_interval(days => greatest(private.setting_int('request_expiry_days', 14), 1));
  if (select count(*) from public.screening_request r
      where r.child_id = new.child_id and r.created_at >= private.wib_day_start())
     >= private.setting_int('request_max_per_child_per_day', 5) then
    raise exception 'limit_exceeded' using errcode = 'DA429';
  end if;
  return new;
end
$$;

drop trigger if exists screening_request_before_insert on public.screening_request;
create trigger screening_request_before_insert
  before insert on public.screening_request
  for each row execute function private.screening_request_before_insert();

-- -----------------------------------------------------------------------------
-- brushing_proof: server-controlled columns, exact object path, size/duration
-- and daily limits (DA429 = limit_exceeded; wrong path = 23514).
-- -----------------------------------------------------------------------------
create or replace function private.brushing_proof_before_insert()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_owner uuid;
  v_ext text;
begin
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('brushing_proof:' || new.child_id::text, 0));
  new.status := 'uploaded';
  new.uploaded_at := now();
  new.expires_at := now() + make_interval(days => greatest(private.setting_int('proof_retention_days', 7), 1));
  new.viewed_at := null;
  new.viewed_by := null;
  new.deleted_at := null;
  new.object_removed_at := null;
  select c.owner_id into v_owner from public.child_profile c where c.id = new.child_id;
  v_ext := case new.mime when 'video/mp4' then 'mp4' when 'video/quicktime' then 'mov' end;
  if v_owner is null or v_ext is null
     or new.object_path is distinct from (v_owner::text || '/' || new.child_id::text || '/' || new.id::text || '.' || v_ext) then
    raise exception 'proof_path_mismatch' using errcode = '23514';
  end if;
  if new.size_bytes > least(private.setting_int('proof_max_bytes', 10485760), 52428800)
     or new.duration_s > private.setting_int('proof_max_duration_s', 180) then
    raise exception 'limit_exceeded' using errcode = 'DA429';
  end if;
  if (select count(*) from public.brushing_proof p
      where p.child_id = new.child_id and p.uploaded_at >= private.wib_day_start())
     >= private.setting_int('proof_max_per_child_per_day', 3) then
    raise exception 'limit_exceeded' using errcode = 'DA429';
  end if;
  return new;
end
$$;

drop trigger if exists brushing_proof_before_insert on public.brushing_proof;
create trigger brushing_proof_before_insert
  before insert on public.brushing_proof
  for each row execute function private.brushing_proof_before_insert();

-- -----------------------------------------------------------------------------
-- consent integrity (redefined, same behaviour) + withdrawal of (d) marks the
-- child's live proofs deleted at once (hidden; sweeper removes the objects).
-- -----------------------------------------------------------------------------
create or replace function private.consent_guard()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if new.id <> old.id or new.child_id <> old.child_id
     or new.consent_type <> old.consent_type or new.text_version <> old.text_version
     or new.method <> old.method or new.granted_at <> old.granted_at then
    raise exception 'consent_immutable' using errcode = '42501';
  end if;
  -- FK "on delete set null" when the granting account is deleted.
  if old.granted_by is not null and new.granted_by is null
     and new.withdrawn_at is not distinct from old.withdrawn_at then
    return new;
  end if;
  if new.granted_by is distinct from old.granted_by or old.withdrawn_at is not null then
    raise exception 'consent_immutable' using errcode = '42501';
  end if;
  if new.withdrawn_at is not null and new.consent_type = 'c' then
    update public.image_upload u
       set status = 'withdrawn', withdrawn_at = now()
     where u.child_id = new.child_id and u.status = 'uploaded';
  end if;
  if new.withdrawn_at is not null and new.consent_type = 'd' then
    update public.brushing_proof p
       set status = 'deleted', deleted_at = now()
     where p.child_id = new.child_id and p.status in ('uploaded', 'viewed');
  end if;
  return new;
end
$$;

-- -----------------------------------------------------------------------------
-- Client RPCs touched by the account model (same signatures)
-- -----------------------------------------------------------------------------

-- request_deletion: child accounts cannot delete themselves or anything else.
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
  if private.is_child_account() then
    raise exception 'forbidden' using errcode = '42501';
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

-- admin_grant_role: the 'child' role is server-provisioned only, and child
-- accounts never receive other roles.
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
  if p_user is null or not exists (select 1 from auth.users u where u.id = p_user) then
    raise exception 'unknown_user' using errcode = '22023';
  end if;
  if p_role = 'child'::public.app_role then
    raise exception 'role_not_grantable' using errcode = '22023';
  end if;
  if exists (select 1 from public.child_account ca where ca.user_id = p_user) then
    raise exception 'role_conflict_child_account' using errcode = '22023';
  end if;
  -- Separation of duties: aggregate/admin accounts must not own child data.
  if p_role in ('facility_viewer', 'admin')
     and exists (select 1 from public.child_profile c where c.owner_id = p_user) then
    raise exception 'role_conflict_owns_children' using errcode = '22023';
  end if;
  insert into public.user_role (user_id, role, facility_code, granted_by)
  values (p_user, p_role, p_facility_code, (select auth.uid()))
  on conflict (user_id, role) do update
    set facility_code = excluded.facility_code, granted_by = excluded.granted_by, granted_at = now();
  if p_role in ('facility_viewer', 'admin') then
    delete from public.user_role ur where ur.user_id = p_user and ur.role in ('guardian', 'kader');
  end if;
  perform private.audit('role_granted', 'user_role', p_user, jsonb_build_object('role', p_role::text));
end
$$;

-- -----------------------------------------------------------------------------
-- Policies (new tables + existing ones that change)
-- -----------------------------------------------------------------------------

-- child_profile: + the child account's own row
drop policy if exists child_profile_select on public.child_profile;
create policy child_profile_select on public.child_profile
  for select to authenticated
  using (private.can_access_child(id) or private.child_can_access(id));

-- brushing_event: + child account reads/records for its own child
drop policy if exists brushing_event_select on public.brushing_event;
create policy brushing_event_select on public.brushing_event
  for select to authenticated
  using (private.can_access_child(child_id) or private.child_can_access(child_id));

drop policy if exists brushing_event_insert on public.brushing_event;
create policy brushing_event_insert on public.brushing_event
  for insert to authenticated
  with check (
    recorded_by = (select auth.uid())
    and (private.can_access_child(child_id) or private.child_can_access(child_id))
    and private.has_active_consent(child_id, 'b')
    and not private.deletion_pending(child_id)
  );

-- screening: request_id (optional) must be a request of the same child
drop policy if exists screening_insert on public.screening;
create policy screening_insert on public.screening
  for insert to authenticated
  with check (
    recorded_by = (select auth.uid())
    and private.can_access_child(child_id)
    and private.has_active_consent(child_id, 'b')
    and not private.deletion_pending(child_id)
    and (request_id is null or exists (
          select 1 from public.screening_request r
          where r.id = request_id and r.child_id = screening.child_id))
  );

-- deletion_request: never by a child account
drop policy if exists deletion_request_insert_own on public.deletion_request;
create policy deletion_request_insert_own on public.deletion_request
  for insert to authenticated
  with check (
    requested_by = (select auth.uid())
    and status = 'pending'
    and not private.is_child_account()
    and ((scope = 'child' and private.owns_child(subject_id))
         or (scope = 'account' and subject_id = (select auth.uid())))
  );

-- child_account: own active row (child) or accounts of own children (guardian)
drop policy if exists child_account_select on public.child_account;
create policy child_account_select on public.child_account
  for select to authenticated
  using (
    (user_id = (select auth.uid()) and disabled_at is null)
    or (guardian_id = (select auth.uid()) and private.owns_child(child_id))
  );
-- no insert/update/delete policies: service_role (Edge Function) + signup trigger only

-- screening_request
drop policy if exists screening_request_select on public.screening_request;
create policy screening_request_select on public.screening_request
  for select to authenticated
  using (private.owns_child(child_id) or private.child_can_access(child_id));

drop policy if exists screening_request_insert on public.screening_request;
create policy screening_request_insert on public.screening_request
  for insert to authenticated
  with check (
    requested_by = (select auth.uid())
    and private.child_can_access(child_id)
    and private.has_active_consent(child_id, 'b')
    and not private.deletion_pending(child_id)
  );
-- status changes only through ack_screening_request / screening_recorded (definer helpers)

-- brushing_proof: live rows only (not deleted/expired/removed, consent d active)
drop policy if exists brushing_proof_select on public.brushing_proof;
create policy brushing_proof_select on public.brushing_proof
  for select to authenticated
  using (
    (private.owns_child(child_id) or private.child_can_access(child_id))
    and status in ('uploaded', 'viewed')
    and object_removed_at is null
    and expires_at > now()
    and private.has_active_consent(child_id, 'd')
  );

drop policy if exists brushing_proof_insert on public.brushing_proof;
create policy brushing_proof_insert on public.brushing_proof
  for insert to authenticated
  with check (
    uploaded_by = (select auth.uid())
    and private.child_can_access(child_id)
    and private.flag_enabled('brushing_proof_video')
    and private.has_active_consent(child_id, 'b')
    and private.has_active_consent(child_id, 'd')
    and not private.deletion_pending(child_id)
    and exists (select 1 from public.brushing_event e
                where e.id = brushing_event_id and e.child_id = brushing_proof.child_id)
  );
-- viewed/deleted only through mark_proof_viewed / delete_brushing_proof (definer)

-- child_login_attempt: no policies (service_role only)

-- -----------------------------------------------------------------------------
-- Grants for helpers referenced by policies / triggers / invoker RPCs
-- -----------------------------------------------------------------------------
revoke all on function
  private.is_child_account(),
  private.child_account_active(),
  private.own_child_id(),
  private.child_can_access(uuid),
  private.account_type(),
  private.is_teen(),
  private.wib_day_start(),
  private.teen_age_ok(smallint, smallint),
  private.provision_child_account(uuid, jsonb),
  private.handle_user_updated(),
  private.child_profile_account_guard(),
  private.screening_request_before_insert(),
  private.brushing_proof_before_insert()
from public, anon;

grant execute on function
  private.is_child_account(),
  private.child_account_active(),
  private.own_child_id(),
  private.child_can_access(uuid),
  private.account_type(),
  private.is_teen(),
  private.wib_day_start()
to authenticated, service_role;

revoke all on function private.provision_child_account(uuid, jsonb) from authenticated, service_role;
revoke all on function private.teen_age_ok(smallint, smallint) from authenticated;
