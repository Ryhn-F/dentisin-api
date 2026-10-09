-- =============================================================================
-- DentisAI · 0800 facility aggregates, audited admin RPCs, keepalive
-- =============================================================================

-- ---------------------------------------------------------------------------
-- facility_viewer: aggregate counts only (FR-110). Month granularity, scoped to
-- the viewer's facility_code, small-cell suppression (k >= 5): cells with
-- fewer than k screenings return screenings = NULL and suppressed = true.
-- Known limitation: no complementary suppression (documented in README).
-- ---------------------------------------------------------------------------
create or replace function public.facility_screening_summary(p_from date, p_to date)
returns table (facility_code text, period_month date, level public.triage_level,
               screenings integer, suppressed boolean)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_admin boolean := private.is_admin();
  v_scope text := private.role_facility_code('facility_viewer');
  v_k integer := greatest(private.setting_int('aggregate_min_cell_size', 5), 5);
  v_from date;
  v_to date;
begin
  if not v_admin and v_scope is null then
    raise exception 'forbidden' using errcode = '42501';
  end if;
  if p_from is null or p_to is null or p_to < p_from or p_to - p_from > 400 then
    raise exception 'invalid_range' using errcode = '22023';
  end if;
  v_from := date_trunc('month', p_from)::date;
  v_to := (date_trunc('month', p_to) + interval '1 month')::date;   -- whole months only

  perform private.audit('aggregate_read', 'screening', null,
                        jsonb_build_object('scope', case when v_admin then 'all' else 'facility' end,
                                           'months', ((v_to - v_from) / 28)));
  return query
    with base as (
      select distinct on (s.id) r.facility_code as fc,
             date_trunc('month', s.screened_at)::date as m,
             s.level as lv
      from public.screening s
      join public.class_roster_member cm on cm.child_id = s.child_id
      join public.class_roster r on r.id = cm.roster_id
      where r.facility_code is not null
        and (v_admin or r.facility_code = v_scope)
        and s.screened_at >= v_from and s.screened_at < v_to
      order by s.id, r.facility_code
    )
    select b.fc, b.m, b.lv,
           case when count(*) >= v_k then count(*)::integer end,
           count(*) < v_k
    from base b
    group by b.fc, b.m, b.lv
    order by b.fc, b.m, b.lv;
end
$$;

revoke all on function public.facility_screening_summary(date, date) from public, anon;
grant execute on function public.facility_screening_summary(date, date) to authenticated;

-- ---------------------------------------------------------------------------
-- Admin RPCs (every call audited; no direct table write grants exist).
-- Bootstrap the FIRST admin from the SQL editor / team PC as the owner:
--   insert into public.user_role (user_id, role) values ('<uuid>', 'admin');
-- ---------------------------------------------------------------------------
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

create or replace function public.admin_revoke_role(p_user uuid, p_role public.app_role)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  if not private.is_admin() then
    raise exception 'forbidden' using errcode = '42501';
  end if;
  if p_role = 'admin' and p_user = (select auth.uid()) then
    raise exception 'cannot_revoke_own_admin' using errcode = '22023';
  end if;
  delete from public.user_role ur where ur.user_id = p_user and ur.role = p_role;
  perform private.audit('role_revoked', 'user_role', p_user, jsonb_build_object('role', p_role::text));
end
$$;

-- Enabling dataset_contribution requires an approval reference (KEPK letter no.).
create or replace function public.admin_set_feature_flag(p_key text, p_enabled boolean, p_approval_ref text default null)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  if not private.is_admin() then
    raise exception 'forbidden' using errcode = '42501';
  end if;
  if p_enabled and p_key = 'dataset_contribution' and p_approval_ref is null then
    raise exception 'approval_ref_required' using errcode = '22023';
  end if;
  update public.feature_flag f
     set enabled = p_enabled,
         approval_ref = coalesce(p_approval_ref, f.approval_ref),
         updated_at = now(),
         updated_by = (select auth.uid())
   where f.key = p_key;
  if not found then
    raise exception 'unknown_flag' using errcode = '22023';
  end if;
  perform private.audit('feature_flag_set', 'feature_flag', null,
                        jsonb_build_object('flag', p_key, 'enabled', p_enabled));
end
$$;

-- Audited, reason-coded read of ONE child record (support / incident handling).
create or replace function public.admin_get_child_record(p_child uuid, p_reason_code text)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v jsonb;
begin
  if not private.is_admin() then
    raise exception 'forbidden' using errcode = '42501';
  end if;
  if p_reason_code is null or p_reason_code !~ '^[a-z][a-z0-9_]{2,39}$' then
    raise exception 'reason_code_required' using errcode = '22023';
  end if;
  perform private.audit('admin_child_read', 'child_profile', p_child,
                        jsonb_build_object('reason', p_reason_code));
  select jsonb_build_object(
    'child', to_jsonb(c) - 'owner_id',
    'consent', coalesce((select jsonb_agg(to_jsonb(x) - 'granted_by') from public.consent x where x.child_id = c.id), '[]'),
    'screening', coalesce((select jsonb_agg(to_jsonb(x) - 'recorded_by') from public.screening x where x.child_id = c.id), '[]'),
    'referral', coalesce((select jsonb_agg(to_jsonb(x) - 'created_by') from public.referral x where x.child_id = c.id), '[]'),
    'brushing_event_count', (select count(*) from public.brushing_event x where x.child_id = c.id),
    'image_upload_count', (select count(*) from public.image_upload x where x.child_id = c.id)
  ) into v
  from public.child_profile c where c.id = p_child;
  return v;
end
$$;

revoke all on function
  public.admin_grant_role(uuid, public.app_role, text),
  public.admin_revoke_role(uuid, public.app_role),
  public.admin_set_feature_flag(text, boolean, text),
  public.admin_get_child_record(uuid, text)
from public, anon;
grant execute on function
  public.admin_grant_role(uuid, public.app_role, text),
  public.admin_revoke_role(uuid, public.app_role),
  public.admin_set_feature_flag(text, boolean, text),
  public.admin_get_child_record(uuid, text)
to authenticated;

-- ---------------------------------------------------------------------------
-- keepalive: target of the external cron ping (ADR 008). No data, anon OK.
-- ---------------------------------------------------------------------------
create or replace function public.keepalive()
returns integer
language sql
stable
security invoker
set search_path = ''
as $$
  select 1
$$;

comment on function public.keepalive() is
  'Returns 1. Pinged by backend/.github/workflows/keepalive.yml (POST /rest/v1/rpc/keepalive with the anon key).';

revoke all on function public.keepalive() from public;
grant execute on function public.keepalive() to anon, authenticated, service_role;
