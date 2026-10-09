-- =============================================================================
-- DentisAI · 0400 Row Level Security, policies and explicit grants
-- =============================================================================
-- Model: DEFAULT DENY. RLS is ENABLED + FORCED on every table (also for the
-- owner). anon gets nothing (only public.keepalive, see 0800). authenticated
-- gets explicit, column-scoped grants; policies decide which rows.
--   guardian        -> only own account + own children
--   kader           -> own rosters + children in own rosters (and own children)
--   facility_viewer -> NO row-level child data; aggregates via RPC only (0800)
--   admin           -> NO direct row access to child data; audited RPCs only
--   service_role    -> bypasses RLS (Edge Functions / team PC only, never in app)

-- 1) Enable + force RLS on every table we own -------------------------------
do $$
declare
  t record;
begin
  for t in
    select n.nspname, c.relname
    from pg_catalog.pg_class c
    join pg_catalog.pg_namespace n on n.oid = c.relnamespace
    where n.nspname in ('public', 'private') and c.relkind in ('r', 'p')
  loop
    execute format('alter table %I.%I enable row level security', t.nspname, t.relname);
    execute format('alter table %I.%I force row level security', t.nspname, t.relname);
  end loop;
end
$$;

-- 2) Baseline privileges -----------------------------------------------------
revoke all on all tables in schema public from anon, authenticated, public;
revoke all on all tables in schema private from anon, authenticated, public, service_role;
revoke all on all sequences in schema public from anon, authenticated, public;
revoke all on all functions in schema private from public, anon, authenticated;

grant select, insert, update, delete on all tables in schema public to service_role;
revoke update, delete, truncate on public.audit_log from service_role;
grant usage, select on all sequences in schema public to service_role;
grant execute on all functions in schema private to service_role;
revoke execute on function private.hash_ref(uuid) from service_role;

-- Validators referenced by CHECK constraints run with the writer's privileges.
grant execute on function
  private.is_version_tag(text),
  private.is_code_array(text[], integer),
  private.is_unit_number(jsonb),
  private.findings_valid(jsonb),
  private.is_safe_detail(jsonb),
  private.try_uuid(text),
  private.jsonb_only_keys(jsonb, text[])
to authenticated;

-- Helpers referenced by policies / triggers run with the caller's privileges.
grant execute on function
  private.has_role(public.app_role),
  private.is_admin(),
  private.role_facility_code(public.app_role),
  private.can_access_child(uuid),
  private.owns_child(uuid),
  private.child_exists(uuid),
  private.has_active_consent(uuid, public.consent_type),
  private.active_consent_id(uuid, public.consent_type),
  private.deletion_pending(uuid),
  private.flag_enabled(text),
  private.setting_int(text, integer),
  private.audit(text, text, uuid, jsonb)
to authenticated;

-- 3) Column-scoped grants for authenticated ----------------------------------
grant select on public.guardian to authenticated;
grant update (locale) on public.guardian to authenticated;

grant select on public.user_role to authenticated;              -- own rows only (policy); NO write grants

grant select on public.child_profile to authenticated;
grant insert (id, owner_id, birth_year, birth_month, allergies) on public.child_profile to authenticated;
grant update (birth_year, birth_month, allergies) on public.child_profile to authenticated;

grant select on public.consent to authenticated;
grant insert (id, child_id, granted_by, consent_type, text_version, method, granted_at) on public.consent to authenticated;
grant update (withdrawn_at) on public.consent to authenticated;

grant select on public.screening to authenticated;
grant insert (id, child_id, recorded_by, screened_at, model_version, rules_version, level, red_flag, findings)
  on public.screening to authenticated;

grant select on public.brushing_event to authenticated;
grant insert (id, child_id, recorded_by, started_at, duration_s, spirit_score, slot, camera_mode)
  on public.brushing_event to authenticated;

grant select on public.referral to authenticated;
grant insert (id, child_id, screening_id, created_by, level, reason_codes, facility_code, referred_at)
  on public.referral to authenticated;

grant select on public.image_upload to authenticated;
grant insert (id, child_id, uploaded_by, consent_id, object_path) on public.image_upload to authenticated;

grant select, delete on public.class_roster to authenticated;
grant insert (id, kader_id, label, school_year, facility_code) on public.class_roster to authenticated;
grant update (label, school_year, facility_code) on public.class_roster to authenticated;

grant select, delete on public.class_roster_member to authenticated;
grant insert (roster_id, child_id, member_label) on public.class_roster_member to authenticated;
grant update (member_label) on public.class_roster_member to authenticated;

grant select on public.deletion_request to authenticated;
grant insert (requested_by, scope, subject_id) on public.deletion_request to authenticated;

grant select on public.audit_log to authenticated;              -- admin only (policy)
grant select on public.sync_event to authenticated;             -- own rows only (policy); writes via definer helper
grant select on public.feature_flag to authenticated;
grant select on public.app_setting to authenticated;            -- admin only (policy)

-- 4) Policies ----------------------------------------------------------------
-- guardian
drop policy if exists guardian_select_own on public.guardian;
create policy guardian_select_own on public.guardian
  for select to authenticated
  using (id = (select auth.uid()));

drop policy if exists guardian_update_own on public.guardian;
create policy guardian_update_own on public.guardian
  for update to authenticated
  using (id = (select auth.uid()))
  with check (id = (select auth.uid()));

-- user_role: read own roles; no client writes (no grants, no policies)
drop policy if exists user_role_select_own on public.user_role;
create policy user_role_select_own on public.user_role
  for select to authenticated
  using (user_id = (select auth.uid()));

-- child_profile
drop policy if exists child_profile_select on public.child_profile;
create policy child_profile_select on public.child_profile
  for select to authenticated
  using (private.can_access_child(id));

-- A new child may only be created as an empty shell (no data) unless consent (b)
-- already exists; in practice the shell is created by the first consent_changed
-- event, and data arrives with child_upserted once (b) is active.
drop policy if exists child_profile_insert on public.child_profile;
create policy child_profile_insert on public.child_profile
  for insert to authenticated
  with check (
    owner_id = (select auth.uid())
    and (private.has_role('guardian') or private.has_role('kader'))
    and birth_year is null and birth_month is null and cardinality(allergies) = 0
  );

drop policy if exists child_profile_update on public.child_profile;
create policy child_profile_update on public.child_profile
  for update to authenticated
  using (owner_id = (select auth.uid())
         and private.has_active_consent(id, 'b')
         and not private.deletion_pending(id))
  with check (owner_id = (select auth.uid()));

-- consent
drop policy if exists consent_select on public.consent;
create policy consent_select on public.consent
  for select to authenticated
  using (private.can_access_child(child_id));

drop policy if exists consent_insert on public.consent;
create policy consent_insert on public.consent
  for insert to authenticated
  with check (
    granted_by = (select auth.uid())
    and private.can_access_child(child_id)
    and (method = 'in_app' or private.has_role('kader'))
    and not private.deletion_pending(child_id)
  );

drop policy if exists consent_withdraw on public.consent;
create policy consent_withdraw on public.consent
  for update to authenticated
  using (private.can_access_child(child_id) and withdrawn_at is null)
  with check (withdrawn_at is not null);

-- screening / brushing_event / referral: read if accessible; write needs consent (b)
drop policy if exists screening_select on public.screening;
create policy screening_select on public.screening
  for select to authenticated
  using (private.can_access_child(child_id));

drop policy if exists screening_insert on public.screening;
create policy screening_insert on public.screening
  for insert to authenticated
  with check (
    recorded_by = (select auth.uid())
    and private.can_access_child(child_id)
    and private.has_active_consent(child_id, 'b')
    and not private.deletion_pending(child_id)
  );

drop policy if exists brushing_event_select on public.brushing_event;
create policy brushing_event_select on public.brushing_event
  for select to authenticated
  using (private.can_access_child(child_id));

drop policy if exists brushing_event_insert on public.brushing_event;
create policy brushing_event_insert on public.brushing_event
  for insert to authenticated
  with check (
    recorded_by = (select auth.uid())
    and private.can_access_child(child_id)
    and private.has_active_consent(child_id, 'b')
    and not private.deletion_pending(child_id)
  );

drop policy if exists referral_select on public.referral;
create policy referral_select on public.referral
  for select to authenticated
  using (private.can_access_child(child_id));

drop policy if exists referral_insert on public.referral;
create policy referral_insert on public.referral
  for insert to authenticated
  with check (
    created_by = (select auth.uid())
    and private.can_access_child(child_id)
    and private.has_active_consent(child_id, 'b')
    and not private.deletion_pending(child_id)
    and (screening_id is null or exists (
          select 1 from public.screening s
          where s.id = screening_id and s.child_id = referral.child_id))
  );

-- image_upload: metadata only; requires consent (c) AND feature flag
drop policy if exists image_upload_select on public.image_upload;
create policy image_upload_select on public.image_upload
  for select to authenticated
  using (private.can_access_child(child_id));

drop policy if exists image_upload_insert on public.image_upload;
create policy image_upload_insert on public.image_upload
  for insert to authenticated
  with check (
    uploaded_by = (select auth.uid())
    and private.can_access_child(child_id)
    and private.has_active_consent(child_id, 'c')
    and private.flag_enabled('dataset_contribution')
    and not private.deletion_pending(child_id)
    and object_path like ((select auth.uid())::text || '/' || child_id::text || '/%')
  );

-- class_roster (kader only, own rosters; facility_code must match own scope)
drop policy if exists class_roster_select on public.class_roster;
create policy class_roster_select on public.class_roster
  for select to authenticated
  using (kader_id = (select auth.uid()) and private.has_role('kader'));

drop policy if exists class_roster_insert on public.class_roster;
create policy class_roster_insert on public.class_roster
  for insert to authenticated
  with check (
    kader_id = (select auth.uid()) and private.has_role('kader')
    and (facility_code is null or facility_code = private.role_facility_code('kader'))
  );

drop policy if exists class_roster_update on public.class_roster;
create policy class_roster_update on public.class_roster
  for update to authenticated
  using (kader_id = (select auth.uid()) and private.has_role('kader'))
  with check (
    kader_id = (select auth.uid()) and private.has_role('kader')
    and (facility_code is null or facility_code = private.role_facility_code('kader'))
  );

drop policy if exists class_roster_delete on public.class_roster;
create policy class_roster_delete on public.class_roster
  for delete to authenticated
  using (kader_id = (select auth.uid()) and private.has_role('kader'));

-- class_roster_member: only inside own roster; only children the kader owns
drop policy if exists class_roster_member_select on public.class_roster_member;
create policy class_roster_member_select on public.class_roster_member
  for select to authenticated
  using (exists (select 1 from public.class_roster r
                 where r.id = roster_id and r.kader_id = (select auth.uid()))
         and private.has_role('kader'));

drop policy if exists class_roster_member_insert on public.class_roster_member;
create policy class_roster_member_insert on public.class_roster_member
  for insert to authenticated
  with check (exists (select 1 from public.class_roster r
                      where r.id = roster_id and r.kader_id = (select auth.uid()))
              and private.has_role('kader')
              and private.owns_child(child_id));

drop policy if exists class_roster_member_update on public.class_roster_member;
create policy class_roster_member_update on public.class_roster_member
  for update to authenticated
  using (exists (select 1 from public.class_roster r
                 where r.id = roster_id and r.kader_id = (select auth.uid()))
         and private.has_role('kader'))
  with check (exists (select 1 from public.class_roster r
                      where r.id = roster_id and r.kader_id = (select auth.uid())));

drop policy if exists class_roster_member_delete on public.class_roster_member;
create policy class_roster_member_delete on public.class_roster_member
  for delete to authenticated
  using (exists (select 1 from public.class_roster r
                 where r.id = roster_id and r.kader_id = (select auth.uid()))
         and private.has_role('kader'));

-- deletion_request: requester sees own; may only create a pending request for
-- a child they own or for their own account.
drop policy if exists deletion_request_select_own on public.deletion_request;
create policy deletion_request_select_own on public.deletion_request
  for select to authenticated
  using (requested_by = (select auth.uid()));

drop policy if exists deletion_request_insert_own on public.deletion_request;
create policy deletion_request_insert_own on public.deletion_request
  for insert to authenticated
  with check (
    requested_by = (select auth.uid())
    and status = 'pending'
    and ((scope = 'child' and private.owns_child(subject_id))
         or (scope = 'account' and subject_id = (select auth.uid())))
  );

-- audit_log: admins read; nobody writes directly (private.audit only)
drop policy if exists audit_log_admin_select on public.audit_log;
create policy audit_log_admin_select on public.audit_log
  for select to authenticated
  using (private.is_admin());

-- sync_event: own ledger rows only
drop policy if exists sync_event_select_own on public.sync_event;
create policy sync_event_select_own on public.sync_event
  for select to authenticated
  using (submitted_by = (select auth.uid()));

-- feature_flag: readable by signed-in users (the app needs it); admin RPC writes
drop policy if exists feature_flag_select on public.feature_flag;
create policy feature_flag_select on public.feature_flag
  for select to authenticated
  using (true);

-- app_setting: admin read; admin/service writes
drop policy if exists app_setting_admin_select on public.app_setting;
create policy app_setting_admin_select on public.app_setting
  for select to authenticated
  using (private.is_admin());

-- private.secret: no policies (deny all; only SECURITY DEFINER owner reads it)

-- 5) image_upload server-controlled columns ----------------------------------
create or replace function private.image_upload_before_insert()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.status := 'uploaded';
  new.uploaded_at := now();
  new.withdrawn_at := null;
  new.deleted_at := null;
  new.bucket_id := 'dataset_photos';
  new.expires_at := now() + make_interval(days => private.setting_int('image_upload_ttl_days', 365));
  return new;
end
$$;

drop trigger if exists image_upload_before_insert on public.image_upload;
create trigger image_upload_before_insert
  before insert on public.image_upload
  for each row execute function private.image_upload_before_insert();
