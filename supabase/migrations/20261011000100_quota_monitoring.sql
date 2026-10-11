-- =============================================================================
-- DentisAI · 20261011000100 free-tier quota monitoring (IMPLEMENTATION_PLAN 5.4)
-- =============================================================================
-- Supabase Free (PRD §9.6, ADR 008) has hard ceilings; running into one stops the
-- product (DB read-only, uploads rejected, sign-up e-mails not sent). This adds:
--   * app_setting rows with the limits and the warn / critical thresholds
--     (readable and changeable without a code change);
--   * private.quota_usage()        -> one jsonb snapshot, COUNTS/BYTES only;
--   * public.admin_quota_usage()   -> admin view (audited, 'quota_read');
--   * public.service_quota_usage() -> service-only, called by the Edge Function
--     quota-check, which the scheduled GitHub workflow turns into an alert.
--
-- Metrics (all approximations of the platform's own billing counters):
--   db_size          pg_database_size(current_database())   vs quota_db_mb
--   storage_size     sum(storage.objects.metadata->>'size') vs quota_storage_mb
--   mau              auth.users with last_sign_in_at in the current calendar month
--                    (UTC) vs quota_mau. The platform counts per billing cycle
--                    [PERLU VERIFIKASI] -> treat as an early-warning signal only.
--   auth_email_24h   GoTrue audit entries that make GoTrue send an e-mail in the
--                    last 24 h vs quota_email_per_day (Resend Free: 100/day).
--                    'unknown' when auth.audit_log_entries is absent or not
--                    readable (hosted projects can disable the Postgres audit
--                    trail [PERLU VERIFIKASI]).
-- Not measurable from SQL: egress (5 GB) and Edge Function invocations -> check
-- the dashboard (Usage page) manually; documented in supabase/README.md.
--
-- Output contains no identifiers, e-mails or paths. RLS untouched (no new
-- tables); every function has search_path = ''. Re-runnable.

insert into public.app_setting (key, value, description) values
  ('quota_db_mb', '500',
   'Batas ukuran database Supabase Free (MB). PRD §9.6.'),
  ('quota_storage_mb', '1024',
   'Batas Storage Supabase Free (MB, semua bucket). PRD §9.6.'),
  ('quota_mau', '50000',
   'Batas pengguna aktif bulanan (MAU) Supabase Free. PRD §9.6.'),
  ('quota_email_per_day', '100',
   'Batas email auth per hari (Resend Free). PRD §9.6.'),
  ('quota_warn_pct', '70',
   'Pemakaian (%) dari batas yang memicu status warn.'),
  ('quota_critical_pct', '90',
   'Pemakaian (%) dari batas yang memicu status critical (workflow quota-check merah).')
on conflict (key) do nothing;

-- GoTrue audit actions that cause an outgoing e-mail (confirmation, recovery,
-- invite, repeated sign-up, re-authentication, e-mail change, magic link).
-- [PERLU VERIFIKASI] against the GoTrue version in use; checked locally on the
-- CLI 2.120 stack for sign-up and recovery (see supabase/README.md).
create or replace function private.quota_email_actions()
returns text[]
language sql
immutable
set search_path = ''
as $$
  select array['user_confirmation_requested', 'user_recovery_requested', 'user_invited',
               'user_repeated_signup', 'user_reauthenticate_requested',
               'user_email_change_requested', 'magiclink_requested']
$$;

-- One metric row: used/limit in the metric's unit, pct (1 decimal) and status.
create or replace function private.quota_metric(
  p_metric text, p_unit text, p_used numeric, p_limit numeric,
  p_warn_pct integer, p_critical_pct integer
)
returns jsonb
language sql
immutable
set search_path = ''
as $$
  select jsonb_build_object(
    'metric', p_metric,
    'unit', p_unit,
    'used', p_used,
    'limit', p_limit,
    'pct', case when p_used is null or p_limit is null or p_limit <= 0 then null
                else round(p_used * 100 / p_limit, 1) end,
    'status', case when p_used is null or p_limit is null or p_limit <= 0 then 'unknown'
                   when p_used * 100 >= p_limit * p_critical_pct then 'critical'
                   when p_used * 100 >= p_limit * p_warn_pct then 'warn'
                   else 'ok' end)
$$;

create or replace function private.quota_usage()
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_warn integer := least(greatest(private.setting_int('quota_warn_pct', 70), 1), 100);
  v_crit integer := least(greatest(private.setting_int('quota_critical_pct', 90), v_warn), 100);
  v_db numeric;
  v_storage numeric;
  v_mau numeric;
  v_email numeric;
  v_metrics jsonb;
  v_overall text;
begin
  v_db := round(pg_catalog.pg_database_size(pg_catalog.current_database())::numeric / 1048576, 1);

  select round(coalesce(sum((o.metadata ->> 'size')::bigint), 0)::numeric / 1048576, 1)
    into v_storage
    from storage.objects o
   where o.metadata ? 'size' and (o.metadata ->> 'size') ~ '^[0-9]{1,18}$';

  select count(*) into v_mau
    from auth.users u
   where u.last_sign_in_at >= date_trunc('month', now() at time zone 'utc') at time zone 'utc';

  begin
    execute $q$select count(*) from auth.audit_log_entries a
                where a.created_at >= now() - interval '24 hours'
                  and a.payload ->> 'action' = any (private.quota_email_actions())$q$
      into v_email;
  exception when undefined_table or insufficient_privilege then
    v_email := null;
  end;

  v_metrics := jsonb_build_array(
    private.quota_metric('db_size', 'mb', v_db, private.setting_int('quota_db_mb', 500), v_warn, v_crit),
    private.quota_metric('storage_size', 'mb', v_storage, private.setting_int('quota_storage_mb', 1024), v_warn, v_crit),
    private.quota_metric('mau', 'users', v_mau, private.setting_int('quota_mau', 50000), v_warn, v_crit),
    private.quota_metric('auth_email_24h', 'emails', v_email, private.setting_int('quota_email_per_day', 100), v_warn, v_crit));

  select case max(case m ->> 'status' when 'critical' then 3 when 'warn' then 2
                                      when 'unknown' then 1 else 0 end)
              when 3 then 'critical' when 2 then 'warn' when 1 then 'unknown' else 'ok' end
    into v_overall
    from jsonb_array_elements(v_metrics) m;

  return jsonb_build_object(
    'checked_at', now(),
    'warn_pct', v_warn,
    'critical_pct', v_crit,
    'overall_status', v_overall,
    'metrics', v_metrics);
end
$$;

create or replace function public.admin_quota_usage()
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v jsonb;
begin
  if not private.is_admin() then
    raise exception 'forbidden' using errcode = '42501';
  end if;
  v := private.quota_usage();
  perform private.audit('quota_read', null, null,
                        jsonb_build_object('overall_status', v ->> 'overall_status'));
  return v;
end
$$;

create or replace function public.service_quota_usage()
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
  perform private.assert_service_role();
  return private.quota_usage();
end
$$;

revoke all on function
  private.quota_email_actions(),
  private.quota_metric(text, text, numeric, numeric, integer, integer),
  private.quota_usage()
from public, anon, authenticated;

revoke all on function public.admin_quota_usage() from public, anon;
grant execute on function public.admin_quota_usage() to authenticated;

revoke all on function public.service_quota_usage() from public, anon, authenticated;
grant execute on function public.service_quota_usage() to service_role;

comment on function public.admin_quota_usage() is
  'Admin only, audited. Free-tier usage snapshot: db_size, storage_size, mau, auth_email_24h with ok/warn/critical/unknown.';
comment on function public.service_quota_usage() is
  'Service only. Same snapshot as admin_quota_usage(); called by the Edge Function quota-check.';
