-- =============================================================================
-- DentisAI · 0300 helper functions (schema private) and triggers
-- =============================================================================
-- All SECURITY DEFINER functions: `set search_path = ''`, fully-qualified names,
-- minimal surface, return booleans/codes only, never raise messages with data.

-- Keyed hash of an id for audit/proofs (HMAC-SHA256 with DB-internal pepper).
create or replace function private.hash_ref(p uuid)
returns text
language sql
stable
security definer
set search_path = ''
as $$
  select case when p is null then null else
    encode(extensions.hmac(convert_to(p::text, 'UTF8'),
                           (select s.value from private.secret s where s.name = 'audit_pepper'),
                           'sha256'), 'hex')
  end
$$;

create or replace function private.has_role(p_role public.app_role)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.user_role ur
    where ur.user_id = (select auth.uid()) and ur.role = p_role
  )
$$;

create or replace function private.is_admin()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select private.has_role('admin'::public.app_role)
$$;

create or replace function private.role_facility_code(p_role public.app_role)
returns text
language sql
stable
security definer
set search_path = ''
as $$
  select ur.facility_code from public.user_role ur
  where ur.user_id = (select auth.uid()) and ur.role = p_role
$$;

-- Owner of the child, or a kader whose own roster contains the child.
create or replace function private.can_access_child(p_child uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select p_child is not null and (
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

create or replace function private.owns_child(p_child uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (select 1 from public.child_profile c
                 where c.id = p_child and c.owner_id = (select auth.uid()))
$$;

-- Existence regardless of owner (used to answer "forbidden" uniformly).
create or replace function private.child_exists(p_child uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (select 1 from public.child_profile c where c.id = p_child)
$$;

create or replace function private.has_active_consent(p_child uuid, p_type public.consent_type)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.consent c
    where c.child_id = p_child and c.consent_type = p_type and c.withdrawn_at is null
  )
$$;

create or replace function private.active_consent_id(p_child uuid, p_type public.consent_type)
returns uuid
language sql
stable
security definer
set search_path = ''
as $$
  select c.id from public.consent c
  where c.child_id = p_child and c.consent_type = p_type and c.withdrawn_at is null
$$;

-- A pending/processing deletion request blocks further sync for the subject.
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
           or (d.scope = 'account' and d.subject_id = (select auth.uid())))
  )
$$;

create or replace function private.flag_enabled(p_key text)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce((select f.enabled from public.feature_flag f where f.key = p_key), false)
$$;

create or replace function private.setting_int(p_key text, p_default integer)
returns integer
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(
    (select (s.value #>> '{}')::integer from public.app_setting s
     where s.key = p_key and jsonb_typeof(s.value) = 'number'),
    p_default)
$$;

create or replace function private.try_uuid(p text)
returns uuid
language plpgsql
immutable
set search_path = ''
as $$
begin
  if p is null or p !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then
    return null;
  end if;
  return lower(p)::uuid;
end
$$;

create or replace function private.jsonb_only_keys(p_obj jsonb, p_allowed text[])
returns boolean
language plpgsql
immutable
set search_path = ''
as $$
declare
  k text;
begin
  if p_obj is null or jsonb_typeof(p_obj) <> 'object' then
    return false;
  end if;
  for k in select jsonb_object_keys(p_obj) loop
    if not (k = any (p_allowed)) then
      return false;
    end if;
  end loop;
  return true;
end
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
     order by case ur.role when 'admin' then 1 when 'facility_viewer' then 2
                           when 'kader' then 3 else 4 end
     limit 1),
    nullif(auth.role(), ''),
    'system')
$$;

-- Append a PII-free audit record. details must satisfy private.is_safe_detail.
create or replace function private.audit(
  p_action text,
  p_target_table text default null,
  p_target uuid default null,
  p_details jsonb default '{}'::jsonb
)
returns void
language sql
security definer
set search_path = ''
as $$
  insert into public.audit_log (actor_role, actor_hash, action, target_table, target_hash, details)
  values (private.current_actor_role(),
          private.hash_ref((select auth.uid())),
          p_action, p_target_table, private.hash_ref(p_target),
          coalesce(p_details, '{}'::jsonb));
$$;

-- -----------------------------------------------------------------------------
-- Signup trigger: every new auth user gets a guardian row and ONLY the
-- 'guardian' role. raw_user_meta_data / app_metadata are deliberately ignored,
-- so clients cannot request a role during signUp().
-- -----------------------------------------------------------------------------
create or replace function private.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.guardian (id) values (new.id) on conflict (id) do nothing;
  insert into public.user_role (user_id, role) values (new.id, 'guardian')
  on conflict (user_id, role) do nothing;
  return new;
end
$$;

drop trigger if exists dentisai_on_auth_user_created on auth.users;
create trigger dentisai_on_auth_user_created
  after insert on auth.users
  for each row execute function private.handle_new_user();

-- -----------------------------------------------------------------------------
-- audit_log is append-only for everyone (including the table owner).
-- -----------------------------------------------------------------------------
create or replace function private.forbid_mutation()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception 'append_only_table' using errcode = '42501';
end
$$;

drop trigger if exists audit_log_append_only on public.audit_log;
create trigger audit_log_append_only
  before update or delete on public.audit_log
  for each row execute function private.forbid_mutation();

drop trigger if exists audit_log_no_truncate on public.audit_log;
create trigger audit_log_no_truncate
  before truncate on public.audit_log
  for each statement execute function private.forbid_mutation();

-- -----------------------------------------------------------------------------
-- consent integrity: only withdrawn_at may change, once. Withdrawing consent (c)
-- immediately marks that child's uploaded photos as withdrawn; the
-- retention-sweep Edge Function then removes the objects from Storage.
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
  return new;
end
$$;

drop trigger if exists consent_guard on public.consent;
create trigger consent_guard
  before update on public.consent
  for each row execute function private.consent_guard();

-- Keep owner_id immutable on child_profile.
create or replace function private.child_profile_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.owner_id <> old.owner_id or new.id <> old.id then
    raise exception 'owner_immutable' using errcode = '42501';
  end if;
  new.updated_at := now();
  return new;
end
$$;

drop trigger if exists child_profile_guard on public.child_profile;
create trigger child_profile_guard
  before update on public.child_profile
  for each row execute function private.child_profile_guard();
