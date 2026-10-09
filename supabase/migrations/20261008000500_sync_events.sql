-- =============================================================================
-- DentisAI · 0500 RPC sync_events(batch jsonb) — idempotent outbox ingestion
-- =============================================================================
-- Contract (FR-100, ADR 005):
--   input : JSON array (1..sync_batch_max_events, <= 256 KiB) of
--           { "event_id": uuid, "type": <sync_event_type>, "payload": {...},
--             "occurred_at"?: timestamptz }
--   output: JSON array, one element per input element, same order:
--           { "index": n, "event_id": uuid|null, "status": "applied"|"rejected",
--             "code"?: <reason> }
--   * Idempotent per event_id: a replay (same caller, same type+payload) returns
--     the stored terminal result; nothing is written twice.
--   * Same event_id with different content, or owned by another account:
--     rejected "event_id_conflict" (not stored, reveals nothing).
--   * Rejections are terminal and stored (deterministic replays); fix the cause
--     and send a NEW event_id. Only "internal_error" is not stored (retryable).
--   * Codes: schema_invalid, unknown_event_type, forbidden, consent_required,
--     feature_disabled, deletion_pending, entity_conflict, event_id_conflict,
--     internal_error.
--   * SECURITY INVOKER: every write is additionally checked by RLS (0400).
--   * Never logs or echoes payload values; error texts are fixed codes.

-- Idempotency ledger access (definer: the ledger has no client write grants) --
create or replace function private.sync_lookup(p_event_id uuid)
returns table (is_mine boolean, payload_sha256 text, result jsonb)
language sql
stable
security definer
set search_path = ''
as $$
  select e.submitted_by = (select auth.uid()), e.payload_sha256, e.result
  from public.sync_event e
  where e.event_id = p_event_id
$$;

create or replace function private.sync_record(
  p_event_id uuid,
  p_type public.sync_event_type,
  p_child uuid,
  p_hash text,
  p_result jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_existing record;
begin
  insert into public.sync_event (event_id, submitted_by, event_type, child_id, payload_sha256, result)
  values (p_event_id, (select auth.uid()), p_type, p_child, p_hash, p_result)
  on conflict (event_id) do nothing;
  if found then
    return p_result;
  end if;
  -- Lost a race against a concurrent identical submission: return the winner.
  select e.submitted_by, e.payload_sha256, e.result into v_existing
  from public.sync_event e where e.event_id = p_event_id;
  if v_existing.submitted_by = (select auth.uid()) and v_existing.payload_sha256 = p_hash then
    return v_existing.result;
  end if;
  return jsonb_build_object('event_id', p_event_id, 'status', 'rejected', 'code', 'event_id_conflict');
end
$$;

-- Does a storage object exist at this path and was it uploaded by the caller?
create or replace function private.own_dataset_object_exists(p_path text)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from storage.objects o
    where o.bucket_id = 'dataset_photos' and o.name = p_path
      and split_part(o.name, '/', 1) = (select auth.uid())::text
  )
$$;

-- Timestamp sanity: parseable, not more than 1 day in the future, not before 2020.
create or replace function private.sane_ts(p text)
returns timestamptz
language plpgsql
stable
set search_path = ''
as $$
declare
  v timestamptz;
begin
  if p is null then
    return null;
  end if;
  v := p::timestamptz;
  if v > now() + interval '1 day' or v < timestamptz '2020-01-01' then
    raise exception 'timestamp_out_of_range' using errcode = '22008';
  end if;
  return v;
end
$$;

create or replace function private.json_text_array(p jsonb)
returns text[]
language plpgsql
immutable
set search_path = ''
as $$
begin
  if p is null or jsonb_typeof(p) = 'null' then
    return '{}'::text[];
  end if;
  if jsonb_typeof(p) <> 'array' then
    raise exception 'array_expected' using errcode = '22023';
  end if;
  return coalesce((select array_agg(x order by o) from jsonb_array_elements_text(p) with ordinality t(x, o)), '{}'::text[]);
end
$$;

-- ---------------------------------------------------------------------------
-- Per-type handlers. Return NULL on success or a rejection code. They only
-- write after all checks pass; RLS re-checks every write.
-- ---------------------------------------------------------------------------
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

  if v_method = 'paper_via_kader' and not private.has_role('kader') then
    return 'forbidden';
  end if;

  if not private.child_exists(p_child) then
    if v_action = 'withdraw' then
      return null;                       -- nothing to withdraw: no-op
    end if;
    if not (private.has_role('guardian') or private.has_role('kader')) then
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

create or replace function private.sync_child_upserted(p_child uuid, p jsonb)
returns text
language plpgsql
security invoker
set search_path = ''
as $$
begin
  if not private.jsonb_only_keys(p, array['child_id', 'birth_year', 'birth_month', 'allergies']) then
    return 'schema_invalid';
  end if;
  if not private.owns_child(p_child) then
    return 'forbidden';
  end if;
  if private.deletion_pending(p_child) then
    return 'deletion_pending';
  end if;
  if not private.has_active_consent(p_child, 'b') then
    return 'consent_required';
  end if;
  update public.child_profile c
     set birth_year  = (p ->> 'birth_year')::smallint,
         birth_month = (p ->> 'birth_month')::smallint,
         allergies   = private.json_text_array(p -> 'allergies')
   where c.id = p_child;
  if not found then
    return 'forbidden';
  end if;
  return null;
end
$$;

-- Shared gate for data events: access, pending deletion, consent (b).
create or replace function private.sync_gate_b(p_child uuid)
returns text
language plpgsql
stable
security invoker
set search_path = ''
as $$
begin
  if not private.can_access_child(p_child) then
    return 'forbidden';
  end if;
  if private.deletion_pending(p_child) then
    return 'deletion_pending';
  end if;
  if not private.has_active_consent(p_child, 'b') then
    return 'consent_required';
  end if;
  return null;
end
$$;

create or replace function private.sync_screening_recorded(p_child uuid, p jsonb)
returns text
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_code text;
begin
  if not private.jsonb_only_keys(p, array['screening_id', 'child_id', 'screened_at', 'model_version',
                                          'rules_version', 'level', 'red_flag', 'findings'])
     or private.try_uuid(p ->> 'screening_id') is null
     or p ->> 'screened_at' is null or p ->> 'level' is null then
    return 'schema_invalid';
  end if;
  v_code := private.sync_gate_b(p_child);
  if v_code is not null then
    return v_code;
  end if;
  insert into public.screening (id, child_id, recorded_by, screened_at, model_version, rules_version,
                                level, red_flag, findings)
  values (private.try_uuid(p ->> 'screening_id'), p_child, (select auth.uid()),
          private.sane_ts(p ->> 'screened_at'), p ->> 'model_version', p ->> 'rules_version',
          (p ->> 'level')::public.triage_level, coalesce((p ->> 'red_flag')::boolean, false),
          coalesce(p -> 'findings', '[]'::jsonb));
  return null;
end
$$;

create or replace function private.sync_brushing_recorded(p_child uuid, p jsonb)
returns text
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_code text;
begin
  if not private.jsonb_only_keys(p, array['brushing_id', 'child_id', 'started_at', 'duration_s',
                                          'spirit_score', 'slot', 'camera_mode'])
     or private.try_uuid(p ->> 'brushing_id') is null
     or p ->> 'started_at' is null or p ->> 'duration_s' is null then
    return 'schema_invalid';
  end if;
  v_code := private.sync_gate_b(p_child);
  if v_code is not null then
    return v_code;
  end if;
  insert into public.brushing_event (id, child_id, recorded_by, started_at, duration_s, spirit_score,
                                     slot, camera_mode)
  values (private.try_uuid(p ->> 'brushing_id'), p_child, (select auth.uid()),
          private.sane_ts(p ->> 'started_at'), (p ->> 'duration_s')::integer,
          (p ->> 'spirit_score')::smallint,
          coalesce(p ->> 'slot', 'other')::public.brushing_slot,
          coalesce((p ->> 'camera_mode')::boolean, false));
  return null;
end
$$;

create or replace function private.sync_referral_created(p_child uuid, p jsonb)
returns text
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_code text;
begin
  if not private.jsonb_only_keys(p, array['referral_id', 'child_id', 'screening_id', 'level',
                                          'reason_codes', 'facility_code', 'referred_at'])
     or private.try_uuid(p ->> 'referral_id') is null
     or p ->> 'level' is null
     or (p ? 'screening_id' and p ->> 'screening_id' is not null
         and private.try_uuid(p ->> 'screening_id') is null) then
    return 'schema_invalid';
  end if;
  v_code := private.sync_gate_b(p_child);
  if v_code is not null then
    return v_code;
  end if;
  insert into public.referral (id, child_id, screening_id, created_by, level, reason_codes,
                               facility_code, referred_at)
  values (private.try_uuid(p ->> 'referral_id'), p_child, private.try_uuid(p ->> 'screening_id'),
          (select auth.uid()), (p ->> 'level')::public.triage_level,
          private.json_text_array(p -> 'reason_codes'), p ->> 'facility_code',
          coalesce(private.sane_ts(p ->> 'referred_at'), now()));
  return null;
end
$$;

create or replace function private.sync_image_upload_registered(p_child uuid, p jsonb)
returns text
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_uid uuid := (select auth.uid());
  v_upload uuid := private.try_uuid(p ->> 'upload_id');
  v_path text := p ->> 'object_path';
begin
  if not private.jsonb_only_keys(p, array['upload_id', 'child_id', 'object_path'])
     or v_upload is null or v_path is null then
    return 'schema_invalid';
  end if;
  if not private.can_access_child(p_child) then
    return 'forbidden';
  end if;
  if private.deletion_pending(p_child) then
    return 'deletion_pending';
  end if;
  if not private.flag_enabled('dataset_contribution') then
    return 'feature_disabled';
  end if;
  if not private.has_active_consent(p_child, 'c') then
    return 'consent_required';
  end if;
  if v_path <> (v_uid::text || '/' || p_child::text || '/' || v_upload::text || '.jpg')
     and v_path <> (v_uid::text || '/' || p_child::text || '/' || v_upload::text || '.jpeg') then
    return 'schema_invalid';
  end if;
  if not private.own_dataset_object_exists(v_path) then
    return 'schema_invalid';
  end if;
  insert into public.image_upload (id, child_id, uploaded_by, consent_id, object_path)
  values (v_upload, p_child, v_uid, private.active_consent_id(p_child, 'c'), v_path);
  return null;
end
$$;

-- ---------------------------------------------------------------------------
-- One event: envelope validation, idempotency, dispatch, error mapping.
-- ---------------------------------------------------------------------------
create or replace function private.sync_apply_one(ev jsonb)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_event_id uuid;
  v_type_txt text;
  v_type public.sync_event_type;
  v_payload jsonb;
  v_hash text;
  v_prior record;
  v_child uuid;
  v_code text;
  v_result jsonb;
begin
  if jsonb_typeof(ev) is distinct from 'object'
     or not private.jsonb_only_keys(ev, array['event_id', 'type', 'payload', 'occurred_at']) then
    return jsonb_build_object('event_id', null, 'status', 'rejected', 'code', 'schema_invalid');
  end if;
  v_event_id := private.try_uuid(ev ->> 'event_id');
  if v_event_id is null then
    return jsonb_build_object('event_id', null, 'status', 'rejected', 'code', 'schema_invalid');
  end if;

  v_type_txt := ev ->> 'type';
  v_payload := ev -> 'payload';
  -- jsonb::text is canonical (sorted keys, normalised whitespace).
  v_hash := encode(extensions.digest(
              convert_to(coalesce(v_type_txt, '') || '|' || coalesce(v_payload::text, 'null'), 'UTF8'),
              'sha256'), 'hex');

  select * into v_prior from private.sync_lookup(v_event_id);
  if found then
    if v_prior.is_mine and v_prior.payload_sha256 = v_hash then
      return v_prior.result;                                   -- idempotent replay
    end if;
    return jsonb_build_object('event_id', v_event_id, 'status', 'rejected', 'code', 'event_id_conflict');
  end if;

  begin
    if v_type_txt is null or v_type_txt not in (
         'consent_changed', 'child_upserted', 'screening_recorded',
         'brushing_recorded', 'referral_created', 'image_upload_registered') then
      v_code := 'unknown_event_type';
    elsif jsonb_typeof(v_payload) is distinct from 'object' or octet_length(v_payload::text) > 8192 then
      v_code := 'schema_invalid';
    else
      v_type := v_type_txt::public.sync_event_type;
      v_child := private.try_uuid(v_payload ->> 'child_id');
      if v_child is null then
        v_code := 'schema_invalid';
      else
        v_code := case v_type
          when 'consent_changed'         then private.sync_consent_changed(v_child, v_payload)
          when 'child_upserted'          then private.sync_child_upserted(v_child, v_payload)
          when 'screening_recorded'      then private.sync_screening_recorded(v_child, v_payload)
          when 'brushing_recorded'       then private.sync_brushing_recorded(v_child, v_payload)
          when 'referral_created'        then private.sync_referral_created(v_child, v_payload)
          when 'image_upload_registered' then private.sync_image_upload_registered(v_child, v_payload)
        end;
      end if;
    end if;
  exception
    when insufficient_privilege then v_code := 'forbidden';            -- RLS backstop
    when unique_violation then v_code := 'entity_conflict';
    when foreign_key_violation then v_code := 'forbidden';
    when check_violation or not_null_violation or invalid_text_representation
         or invalid_parameter_value or datetime_field_overflow or invalid_datetime_format
         or numeric_value_out_of_range or string_data_right_truncation
         or invalid_binary_representation or data_exception then
      v_code := 'schema_invalid';
    when others then
      -- Unexpected: do not store, do not echo SQLERRM (could contain values).
      return jsonb_build_object('event_id', v_event_id, 'status', 'rejected', 'code', 'internal_error');
  end;

  if v_code is null then
    v_result := jsonb_build_object('event_id', v_event_id, 'status', 'applied');
  else
    v_result := jsonb_build_object('event_id', v_event_id, 'status', 'rejected', 'code', v_code);
    v_child := null;     -- rejected events keep no link to a child
  end if;

  return private.sync_record(v_event_id, v_type, v_child, v_hash, v_result);
end
$$;

-- ---------------------------------------------------------------------------
-- Public RPC
-- ---------------------------------------------------------------------------
create or replace function public.sync_events(batch jsonb)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_max integer := least(private.setting_int('sync_batch_max_events', 100), 500);
  v_out jsonb := '[]'::jsonb;
  v_ev jsonb;
  v_idx bigint;
  v_applied integer := 0;
  v_rejected integer := 0;
  v_res jsonb;
begin
  if (select auth.uid()) is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if batch is null or jsonb_typeof(batch) <> 'array' then
    raise exception 'batch_must_be_array' using errcode = '22023';
  end if;
  if jsonb_array_length(batch) < 1 or jsonb_array_length(batch) > v_max then
    raise exception 'batch_size_out_of_range' using errcode = '22023';
  end if;
  if octet_length(batch::text) > 262144 then
    raise exception 'batch_too_large' using errcode = '22023';
  end if;

  for v_ev, v_idx in select e, o from jsonb_array_elements(batch) with ordinality as t(e, o) loop
    v_res := private.sync_apply_one(v_ev);
    if v_res ->> 'status' = 'applied' then
      v_applied := v_applied + 1;
    else
      v_rejected := v_rejected + 1;
    end if;
    v_out := v_out || jsonb_build_array(jsonb_build_object('index', v_idx - 1) || v_res);
  end loop;

  perform private.audit('sync_batch', 'sync_event', null,
                        jsonb_build_object('events', jsonb_array_length(batch),
                                           'applied_or_replayed', v_applied,
                                           'rejected', v_rejected));
  return v_out;
end
$$;

comment on function public.sync_events(jsonb) is
  'Idempotent outbox ingestion (FR-100). See migration 0500 header for the contract.';

revoke all on function public.sync_events(jsonb) from public, anon;
grant execute on function public.sync_events(jsonb) to authenticated;

revoke all on function
  private.sync_lookup(uuid),
  private.sync_record(uuid, public.sync_event_type, uuid, text, jsonb),
  private.own_dataset_object_exists(text),
  private.sane_ts(text),
  private.json_text_array(jsonb),
  private.sync_consent_changed(uuid, jsonb),
  private.sync_child_upserted(uuid, jsonb),
  private.sync_gate_b(uuid),
  private.sync_screening_recorded(uuid, jsonb),
  private.sync_brushing_recorded(uuid, jsonb),
  private.sync_referral_created(uuid, jsonb),
  private.sync_image_upload_registered(uuid, jsonb),
  private.sync_apply_one(jsonb)
from public, anon;

grant execute on function
  private.sync_lookup(uuid),
  private.sync_record(uuid, public.sync_event_type, uuid, text, jsonb),
  private.own_dataset_object_exists(text),
  private.sane_ts(text),
  private.json_text_array(jsonb),
  private.sync_consent_changed(uuid, jsonb),
  private.sync_child_upserted(uuid, jsonb),
  private.sync_gate_b(uuid),
  private.sync_screening_recorded(uuid, jsonb),
  private.sync_brushing_recorded(uuid, jsonb),
  private.sync_referral_created(uuid, jsonb),
  private.sync_image_upload_registered(uuid, jsonb),
  private.sync_apply_one(jsonb)
to authenticated, service_role;
