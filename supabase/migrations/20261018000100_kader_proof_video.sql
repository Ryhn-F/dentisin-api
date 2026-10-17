-- =============================================================================
-- DentisAI · 20261018000100 kader proof video + no child accounts for kader (ADR 017 amendment)
-- =============================================================================
-- Owner decisions (2026-10-18):
--   1. A kader can NOT create child accounts (a kader's pupils are children the
--      kader owns; parents create child accounts for their own children).
--   2. The brushing proof video feature is also available to the kader, as
--      history: the kader records the brushing session of an OWN (roster) child
--      from the kader's own account, registers the clip, uploads it, and watches
--      it again later (child_history kind brushing_proof, list_brushing_proofs,
--      signed URL). Same gates as for child accounts: flag brushing_proof_video,
--      consent (b) AND (d) of that child (the kader may record the paper consent
--      with consent_changed method = paper_via_kader), no pending deletion, size /
--      duration / daily limits, 7-day retention. Still no access for facility
--      viewers and admins; guardians/teens still never register proofs.
--
-- Changes:
--   private.can_register_proof(child)     child account (own child) OR kader owner
--   private.provision_child_account       refuses a guardian that holds the kader role
--   private.sync_brushing_proof_registered  uses can_register_proof
--   policy brushing_proof_insert           uses can_register_proof
--   private.proof_upload_allowed           uses can_register_proof (kader uploads own path)
--   public.my_account                       + capabilities
-- Re-runnable.

-- -----------------------------------------------------------------------------
-- Who may register / upload a proof for a child
-- -----------------------------------------------------------------------------
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
       or (not private.is_child_account()
           and private.has_role('kader'::public.app_role)
           and private.owns_child(p_child))
     )
$$;

revoke all on function private.can_register_proof(uuid) from public, anon;
grant execute on function private.can_register_proof(uuid) to authenticated, service_role;

-- -----------------------------------------------------------------------------
-- Child accounts are never created for (or by) a kader
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
  -- The guardian must be a guardian-type account (not teen, not child, not kader)
  -- that owns the (non-self) child; nothing may be pending deletion.
  if not exists (select 1 from public.guardian g
                 where g.id = v_guardian and g.account_type = 'guardian'::public.account_type)
     or exists (select 1 from public.user_role ur
                where ur.user_id = v_guardian and ur.role = 'kader'::public.app_role)
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
-- sync_events: brushing_proof_registered also from a kader (own child)
-- -----------------------------------------------------------------------------
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
  v_code text;
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
    return 'forbidden';                     -- the child's own account, or the kader that owns the child
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
-- Direct inserts (PostgREST) follow the same rule
-- -----------------------------------------------------------------------------
drop policy if exists brushing_proof_insert on public.brushing_proof;
create policy brushing_proof_insert on public.brushing_proof
  for insert to authenticated
  with check (
    uploaded_by = (select auth.uid())
    and private.can_register_proof(child_id)
    and private.flag_enabled('brushing_proof_video')
    and private.has_active_consent(child_id, 'b')
    and private.has_active_consent(child_id, 'd')
    and not private.deletion_pending(child_id)
    and exists (select 1 from public.brushing_event e
                where e.id = brushing_event_id and e.child_id = brushing_proof.child_id)
  );

-- -----------------------------------------------------------------------------
-- Storage: the uploader (child account or kader owner) writes only the registered path
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
  v_child := private.try_uuid(split_part(p_name, '/', 2));
  if v_child is null or not private.can_register_proof(v_child) then
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

-- -----------------------------------------------------------------------------
-- my_account(): + capabilities (what the UI may offer)
--   create_child_accounts  guardian-type account WITHOUT the kader role
--   register_proofs        child account with proof_allowed, or a kader while the flag is on
-- -----------------------------------------------------------------------------
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
  v_kader boolean;
  v_proof boolean := false;
begin
  if v_uid is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  v_type := private.account_type();
  select coalesce(array_agg(ur.role::text order by ur.role::text), '{}'::text[]) into v_roles
  from public.user_role ur where ur.user_id = v_uid;
  v_kader := 'kader' = any (v_roles);

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
    v_proof := v_kader and private.flag_enabled('brushing_proof_video');
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
      'create_child_accounts', v_type = 'guardian' and not v_kader,
      'register_proofs', v_proof),
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
  'Account facts for app routing (ADR 017): type, roles, child_account, flags, capabilities, limits.';

-- The kader that owns the child is the owner: list_brushing_proofs, mark_proof_viewed,
-- delete_brushing_proof, child_history (kind brushing_proof) and the Storage SELECT policy
-- already work through private.owns_child().
