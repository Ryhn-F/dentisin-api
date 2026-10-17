-- =============================================================================
-- DentisAI · 20261018000300 teen age window 15..19 (ADR 018 amendment)
-- =============================================================================
-- Owner decision (2026-10-18): a teen account (registers for themself) is for
-- ages 15..19. Below 15 a guardian is required: the parent registers as a
-- guardian and creates a child account (ADR 017). The window is enforced on the
-- self profile (ensure_self_profile and the child_profile guard trigger) through
-- private.teen_age_ok(); the age comes from the year(+month) of birth the user
-- enters, so it is self-declared and cannot be verified by the server.
--   * app_setting teen_min_age 10 -> 15 (only while it still holds the old default)
--   * private.teen_age_ok / public.my_account: fallback defaults 10 -> 15
-- Re-runnable.

update public.app_setting
   set value = '15'::jsonb,
       description = 'ADR 018: usia minimum akun remaja (tahun, dari tahun-bulan lahir profil diri). Di bawah itu wajib akun orang tua (guardian) + akun anak.',
       updated_at = now()
 where key = 'teen_min_age' and value = '10'::jsonb;

update public.app_setting
   set description = 'ADR 017/018: usia maksimum akun remaja (tahun, dari tahun-bulan lahir profil diri).'
 where key = 'teen_max_age';

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
  v_min integer := private.setting_int('teen_min_age', 15);
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

-- my_account(): only the fallback default of teen_min_age changes (15).
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
      'teen_min_age', private.setting_int('teen_min_age', 15),
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
