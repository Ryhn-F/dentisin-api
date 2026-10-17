-- =============================================================================
-- DentisAI · 20261017000500 account model v2 (ADR 017) — part 5/6: client RPCs
-- =============================================================================
-- All RPCs: authenticated only (anon revoked), search_path = '', authorisation
-- inside the function. "Not yours" and "does not exist" raise the SAME error
-- (42501 'forbidden'), so another account's child/request/proof cannot be
-- probed. Errors carry fixed codes only (never values).
--
--   my_account()                                            jsonb
--   ensure_self_profile(p_child_id, p_birth_year, p_birth_month)  uuid (teen)
--   list_children()                                         setof row (guardian/teen/kader-owner)
--   child_history(p_child, p_before, p_limit, p_kinds, p_from)    jsonb
--   brushing_streak(p_child, p_tz)                          jsonb
--   list_screening_requests(p_child, p_status, p_limit)     setof row
--   ack_screening_request(p_id, p_action)                   jsonb (guardian/teen)
--   list_brushing_proofs(p_child, p_before, p_limit)        setof row (guardian/teen)
--   mark_proof_viewed(p_proof)                              jsonb (guardian/teen)
--   delete_brushing_proof(p_proof)                          jsonb (guardian/teen)
-- Keyset pagination (child_history, list_brushing_proofs): items are ordered by
-- time DESC; a page never splits items that share the boundary timestamp (it may
-- therefore hold slightly more than p_limit items); pass the returned
-- next_before / the oldest returned timestamp as p_before for the next page.
-- Re-runnable.

-- -----------------------------------------------------------------------------
-- my_account(): who am I (account type, roles, child-account facts, limits)
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
      v_child := jsonb_build_object(
        'child_id', v_ca.child_id,
        'guardian_id', v_ca.guardian_id,
        'username', v_ca.username,
        'disabled', v_ca.disabled,
        'sync_allowed', not v_ca.disabled
                        and private.has_active_consent(v_ca.child_id, 'b')
                        and not private.deletion_pending(v_ca.child_id),
        'proof_allowed', not v_ca.disabled
                         and private.flag_enabled('brushing_proof_video')
                         and private.has_active_consent(v_ca.child_id, 'b')
                         and private.has_active_consent(v_ca.child_id, 'd')
                         and not private.deletion_pending(v_ca.child_id));
    end if;
  else
    select c.id into v_self from public.child_profile c where c.owner_id = v_uid and c.is_self;
    select count(*)::integer into v_count from public.child_account ca where ca.guardian_id = v_uid;
  end if;

  return jsonb_build_object(
    'user_id', v_uid,
    'account_type', v_type,
    'roles', to_jsonb(v_roles),
    'self_child_id', v_self,
    'child_account', v_child,
    'child_accounts', v_count,
    'flags', jsonb_build_object('brushing_proof_video', private.flag_enabled('brushing_proof_video')),
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

-- -----------------------------------------------------------------------------
-- ensure_self_profile(): teen only. Creates the single self profile (is_self)
-- with year(+month) of birth inside [teen_min_age, teen_max_age]; idempotent.
-- If a self profile already exists, its id is returned (the client must adopt
-- it) and the birth data is updated (re-validated).
-- Errors: 28000 not_authenticated · 42501 forbidden (not a teen, or the id is
-- already used by another profile) · 22023 invalid_argument | age_out_of_range
-- | deletion_pending.
-- -----------------------------------------------------------------------------
create or replace function public.ensure_self_profile(
  p_child_id uuid,
  p_birth_year smallint,
  p_birth_month smallint default null
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := (select auth.uid());
  v_self uuid;
begin
  if v_uid is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if not private.is_teen() or not private.has_role('guardian'::public.app_role) then
    raise exception 'forbidden' using errcode = '42501';
  end if;
  if p_child_id is null or p_birth_year is null
     or (p_birth_month is not null and p_birth_month not between 1 and 12) then
    raise exception 'invalid_argument' using errcode = '22023';
  end if;
  if not private.teen_age_ok(p_birth_year, p_birth_month) then
    raise exception 'age_out_of_range' using errcode = '22023';
  end if;

  select c.id into v_self from public.child_profile c where c.owner_id = v_uid and c.is_self;
  if found then
    if private.deletion_pending(v_self) then
      raise exception 'deletion_pending' using errcode = '22023';
    end if;
    update public.child_profile c
       set birth_year = p_birth_year, birth_month = p_birth_month
     where c.id = v_self
       and (c.birth_year is distinct from p_birth_year or c.birth_month is distinct from p_birth_month);
    return v_self;
  end if;

  if exists (select 1 from public.child_profile c where c.id = p_child_id) then
    raise exception 'forbidden' using errcode = '42501';
  end if;
  if exists (select 1 from public.deletion_request d
             where d.status in ('pending', 'processing') and d.scope = 'account' and d.subject_id = v_uid) then
    raise exception 'deletion_pending' using errcode = '22023';
  end if;
  insert into public.child_profile (id, owner_id, birth_year, birth_month, is_self)
  values (p_child_id, v_uid, p_birth_year, p_birth_month, true);
  return p_child_id;
end
$$;

-- -----------------------------------------------------------------------------
-- list_children(): home-screen summary of the caller's OWN children (guardian,
-- teen = its self profile, kader = owned class children). Child accounts: 42501.
-- -----------------------------------------------------------------------------
create or replace function public.list_children()
returns table (
  child_id                uuid,
  is_self                 boolean,
  birth_year              smallint,
  birth_month             smallint,
  consents                text[],
  has_child_account       boolean,
  child_account_disabled  boolean,
  username                text,
  last_screening_at       timestamptz,
  last_screening_level    public.triage_level,
  last_brushing_at        timestamptz,
  open_requests           integer,
  unseen_requests         integer,
  unviewed_proofs         integer,
  deletion_pending        boolean
)
language plpgsql
stable
security invoker
set search_path = ''
as $$
#variable_conflict use_column
begin
  if (select auth.uid()) is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if private.is_child_account() then
    raise exception 'forbidden' using errcode = '42501';
  end if;
  return query
    select c.id,
           c.is_self,
           c.birth_year,
           c.birth_month,
           coalesce((select array_agg(k.consent_type::text order by k.consent_type::text)
                     from public.consent k where k.child_id = c.id and k.withdrawn_at is null), '{}'::text[]),
           ca.user_id is not null,
           case when ca.user_id is null then null else ca.disabled_at is not null end,
           ca.username,
           ls.screened_at,
           ls.level,
           (select max(e.started_at) from public.brushing_event e where e.child_id = c.id),
           (select count(*)::integer from public.screening_request r
             where r.child_id = c.id and r.status in ('pending', 'seen') and r.expires_at > now()),
           (select count(*)::integer from public.screening_request r
             where r.child_id = c.id and r.status = 'pending' and r.expires_at > now()),
           (select count(*)::integer from public.brushing_proof p
             where p.child_id = c.id and p.status = 'uploaded' and p.expires_at > now()
               and p.object_removed_at is null and private.has_active_consent(c.id, 'd')),
           private.deletion_pending(c.id)
    from public.child_profile c
    left join public.child_account ca on ca.child_id = c.id
    left join lateral (
      select s.screened_at, s.level from public.screening s
      where s.child_id = c.id order by s.screened_at desc, s.id desc limit 1) ls on true
    where c.owner_id = (select auth.uid())
    order by c.is_self desc, c.created_at, c.id;
end
$$;

-- -----------------------------------------------------------------------------
-- child_history(): merged timeline of one child.
-- kinds: screening | brushing | referral | screening_request | brushing_proof.
-- Owner (guardian/teen): all kinds. Kader via roster: screening, brushing,
-- referral only. Child account (own child only): brushing, screening_request,
-- brushing_proof. `data` holds codes/numbers/ids only: no PII, no username, no
-- storage path/URL. Result: {items:[{kind,id,occurred_at,data}], next_before}.
-- -----------------------------------------------------------------------------
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
  else
    if not private.can_access_child(p_child) then
      raise exception 'forbidden' using errcode = '42501';
    end if;
    if not private.owns_child(p_child) then
      v_kinds := array(select k from unnest(v_kinds) k where k in ('screening', 'brushing', 'referral'));
    end if;
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

-- -----------------------------------------------------------------------------
-- brushing_streak(): a local calendar day (p_tz, validated against
-- pg_timezone_names) counts when >= 1 brushing_event with duration_s >=
-- streak_min_duration_s started on it. current = consecutive counted days
-- ending today, or ending yesterday when today is not (yet) counted.
-- Returns {current, longest, last_day, today_done, week:[{date, done,
-- brushings}] (7 days, oldest first, ending today), min_duration_s, tz}.
-- -----------------------------------------------------------------------------
create or replace function public.brushing_streak(p_child uuid, p_tz text default 'Asia/Jakarta')
returns jsonb
language plpgsql
stable
security invoker
set search_path = ''
as $$
declare
  v_min integer := private.setting_int('streak_min_duration_s', 60);
  v_today date;
  v_current integer := 0;
  v_longest integer := 0;
  v_last date;
  v_today_done boolean;
  v_week jsonb;
begin
  if (select auth.uid()) is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if p_tz is null or not exists (select 1 from pg_catalog.pg_timezone_names z where z.name = p_tz) then
    raise exception 'invalid_timezone' using errcode = '22023';
  end if;
  if not (private.can_access_child(p_child) or private.child_can_access(p_child)) then
    raise exception 'forbidden' using errcode = '42501';
  end if;
  v_today := (now() at time zone p_tz)::date;

  with days as (
    select distinct (e.started_at at time zone p_tz)::date as d
    from public.brushing_event e
    where e.child_id = p_child and e.duration_s >= v_min
      and (e.started_at at time zone p_tz)::date <= v_today
  ),
  islands as (
    select d, d - (row_number() over (order by d))::integer as grp from days
  ),
  runs as (
    select max(d) as last_d, count(*)::integer as n from islands group by grp
  )
  select coalesce(max(n), 0),
         max(last_d),
         coalesce(max(n) filter (where last_d = v_today), max(n) filter (where last_d = v_today - 1), 0)
    into v_longest, v_last, v_current
  from runs;

  v_today_done := v_last is not distinct from v_today;

  select jsonb_agg(jsonb_build_object('date', g.d, 'done', coalesce(x.n, 0) > 0, 'brushings', coalesce(x.n, 0))
                   order by g.d)
    into v_week
  from (select (v_today - k) as d from generate_series(0, 6) k) g
  left join (
    select (e.started_at at time zone p_tz)::date as d, count(*)::integer as n
    from public.brushing_event e
    where e.child_id = p_child and e.duration_s >= v_min
      and (e.started_at at time zone p_tz)::date between v_today - 6 and v_today
    group by 1
  ) x on x.d = g.d;

  return jsonb_build_object('current', v_current, 'longest', v_longest, 'last_day', v_last,
                            'today_done', v_today_done, 'week', v_week,
                            'min_duration_s', v_min, 'tz', p_tz);
end
$$;

-- -----------------------------------------------------------------------------
-- list_screening_requests(): guardian/teen -> requests of own children
-- (optionally one child); child account -> requests of its own child.
-- status = effective status (pending/seen past expires_at -> expired).
-- -----------------------------------------------------------------------------
create or replace function public.list_screening_requests(
  p_child uuid default null,
  p_status public.request_status[] default null,
  p_limit integer default 30
)
returns table (
  id            uuid,
  child_id      uuid,
  reason_code   text,
  pain_level    smallint,
  status        public.request_status,
  created_at    timestamptz,
  seen_at       timestamptz,
  responded_at  timestamptz,
  screening_id  uuid,
  expires_at    timestamptz
)
language plpgsql
stable
security invoker
set search_path = ''
as $$
#variable_conflict use_column
declare
  v_uid uuid := (select auth.uid());
  v_limit integer := least(greatest(coalesce(p_limit, 30), 1), 100);
  v_child_mode boolean := private.is_child_account();
  v_own uuid;
begin
  if v_uid is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if v_child_mode then
    v_own := private.own_child_id();
    if v_own is null or (p_child is not null and p_child <> v_own) then
      raise exception 'forbidden' using errcode = '42501';
    end if;
  elsif p_child is not null and not private.owns_child(p_child) then
    raise exception 'forbidden' using errcode = '42501';
  end if;

  return query
    select q.id, q.child_id, q.reason_code, q.pain_level, q.eff_status, q.created_at, q.seen_at,
           q.responded_at, q.screening_id, q.expires_at
    from (
      select r.id, r.child_id, r.reason_code, r.pain_level,
             case when r.status in ('pending', 'seen') and r.expires_at <= now()
                  then 'expired'::public.request_status else r.status end as eff_status,
             r.created_at, r.seen_at, r.responded_at, r.screening_id, r.expires_at
      from public.screening_request r
      join public.child_profile c on c.id = r.child_id
      where case when v_child_mode then r.child_id = v_own
                 else c.owner_id = v_uid and (p_child is null or r.child_id = p_child) end
    ) q
    where p_status is null or q.eff_status = any (p_status)
    order by q.created_at desc, q.id desc
    limit v_limit;
end
$$;

-- -----------------------------------------------------------------------------
-- ack_screening_request(p_id, 'seen' | 'cancel'): guardian/teen of the child.
-- Idempotent; returns the resulting {id, status, seen_at, responded_at}.
-- pending -> seen; pending|seen -> cancelled; an expired (by time) request is
-- marked expired; done/cancelled/expired stay unchanged.
-- -----------------------------------------------------------------------------
create or replace function public.ack_screening_request(p_id uuid, p_action text)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  r public.screening_request%rowtype;
begin
  if (select auth.uid()) is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if p_action is null or p_action not in ('seen', 'cancel') then
    raise exception 'invalid_action' using errcode = '22023';
  end if;
  if private.is_child_account() then
    raise exception 'forbidden' using errcode = '42501';
  end if;
  select * into r from public.screening_request q where q.id = p_id for update;
  if not found or not private.owns_child(r.child_id) then
    raise exception 'forbidden' using errcode = '42501';
  end if;

  if r.status in ('pending', 'seen') and r.expires_at <= now() then
    update public.screening_request q set status = 'expired' where q.id = r.id;
  elsif p_action = 'seen' and r.status = 'pending' then
    update public.screening_request q set status = 'seen', seen_at = now() where q.id = r.id;
  elsif p_action = 'cancel' and r.status in ('pending', 'seen') then
    update public.screening_request q set status = 'cancelled', responded_at = now() where q.id = r.id;
  end if;

  select * into r from public.screening_request q where q.id = p_id;
  return jsonb_build_object('id', r.id, 'status', r.status, 'seen_at', r.seen_at, 'responded_at', r.responded_at);
end
$$;

-- -----------------------------------------------------------------------------
-- list_brushing_proofs(): guardian/teen owner of the child. Live proofs only
-- (status uploaded|viewed, not expired, object not removed, consent (d) active).
-- object_path is returned so the app can call
-- storage.from('brushing_proofs').createSignedUrl(object_path, <= 300).
-- object_present = the upload has actually arrived in Storage.
-- -----------------------------------------------------------------------------
create or replace function public.list_brushing_proofs(
  p_child uuid,
  p_before timestamptz default null,
  p_limit integer default 30
)
returns table (
  id                 uuid,
  child_id           uuid,
  brushing_event_id  uuid,
  object_path        text,
  mime               text,
  size_bytes         integer,
  duration_s         integer,
  status             public.proof_status,
  uploaded_at        timestamptz,
  expires_at         timestamptz,
  viewed_at          timestamptz,
  object_present     boolean
)
language plpgsql
stable
security definer
set search_path = ''
as $$
#variable_conflict use_column
declare
  v_limit integer := least(greatest(coalesce(p_limit, 30), 1), 100);
begin
  if (select auth.uid()) is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if private.is_child_account() or not private.owns_child(p_child) then
    raise exception 'forbidden' using errcode = '42501';
  end if;
  return query
    with base as (
      select p.* from public.brushing_proof p
      where p.child_id = p_child
        and p.status in ('uploaded', 'viewed') and p.expires_at > now() and p.object_removed_at is null
        and private.has_active_consent(p.child_id, 'd')
        and (p_before is null or p.uploaded_at < p_before)
    ),
    b as (
      select x.uploaded_at as boundary from base x order by x.uploaded_at desc limit 1 offset (v_limit - 1)
    )
    select base.id, base.child_id, base.brushing_event_id, base.object_path, base.mime, base.size_bytes,
           base.duration_s, base.status, base.uploaded_at, base.expires_at, base.viewed_at,
           exists (select 1 from storage.objects o
                   where o.bucket_id = 'brushing_proofs' and o.name = base.object_path)
    from base left join b on true
    where b.boundary is null or base.uploaded_at >= b.boundary
    order by base.uploaded_at desc, base.id desc;
end
$$;

-- -----------------------------------------------------------------------------
-- mark_proof_viewed(): guardian/teen owner; live proofs only. First view is
-- audited (hashed ids only). Returns {id, status, viewed_at}.
-- -----------------------------------------------------------------------------
create or replace function public.mark_proof_viewed(p_proof uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  p public.brushing_proof%rowtype;
begin
  if (select auth.uid()) is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if private.is_child_account() then
    raise exception 'forbidden' using errcode = '42501';
  end if;
  select * into p from public.brushing_proof x where x.id = p_proof for update;
  if not found or not private.owns_child(p.child_id)
     or p.status not in ('uploaded', 'viewed') or p.expires_at <= now() or p.object_removed_at is not null
     or not private.has_active_consent(p.child_id, 'd') then
    raise exception 'forbidden' using errcode = '42501';
  end if;
  update public.brushing_proof x
     set status = 'viewed', viewed_at = coalesce(x.viewed_at, now()), viewed_by = coalesce(x.viewed_by, (select auth.uid()))
   where x.id = p.id;
  if p.viewed_at is null then
    perform private.audit('brushing_proof_viewed', 'brushing_proof', p.id, '{}'::jsonb);
  end if;
  select * into p from public.brushing_proof x where x.id = p_proof;
  return jsonb_build_object('id', p.id, 'status', p.status, 'viewed_at', p.viewed_at);
end
$$;

-- -----------------------------------------------------------------------------
-- delete_brushing_proof(): guardian/teen owner. Marks deleted (hidden at once);
-- the retention sweeper removes the Storage object, then the row. Idempotent.
-- Returns {id, status, deleted_at}.
-- -----------------------------------------------------------------------------
create or replace function public.delete_brushing_proof(p_proof uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  p public.brushing_proof%rowtype;
begin
  if (select auth.uid()) is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if private.is_child_account() then
    raise exception 'forbidden' using errcode = '42501';
  end if;
  select * into p from public.brushing_proof x where x.id = p_proof for update;
  if not found or not private.owns_child(p.child_id) then
    raise exception 'forbidden' using errcode = '42501';
  end if;
  if p.status in ('uploaded', 'viewed') then
    update public.brushing_proof x set status = 'deleted', deleted_at = now() where x.id = p.id;
    perform private.audit('brushing_proof_deleted', 'brushing_proof', p.id, '{}'::jsonb);
  end if;
  select * into p from public.brushing_proof x where x.id = p_proof;
  return jsonb_build_object('id', p.id, 'status', p.status, 'deleted_at', p.deleted_at);
end
$$;

-- -----------------------------------------------------------------------------
-- Grants
-- -----------------------------------------------------------------------------
revoke all on function
  public.my_account(),
  public.ensure_self_profile(uuid, smallint, smallint),
  public.list_children(),
  public.child_history(uuid, timestamptz, integer, text[], timestamptz),
  public.brushing_streak(uuid, text),
  public.list_screening_requests(uuid, public.request_status[], integer),
  public.ack_screening_request(uuid, text),
  public.list_brushing_proofs(uuid, timestamptz, integer),
  public.mark_proof_viewed(uuid),
  public.delete_brushing_proof(uuid)
from public, anon;

grant execute on function
  public.my_account(),
  public.ensure_self_profile(uuid, smallint, smallint),
  public.list_children(),
  public.child_history(uuid, timestamptz, integer, text[], timestamptz),
  public.brushing_streak(uuid, text),
  public.list_screening_requests(uuid, public.request_status[], integer),
  public.ack_screening_request(uuid, text),
  public.list_brushing_proofs(uuid, timestamptz, integer),
  public.mark_proof_viewed(uuid),
  public.delete_brushing_proof(uuid)
to authenticated;
