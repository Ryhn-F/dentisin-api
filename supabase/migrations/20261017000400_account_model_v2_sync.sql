-- =============================================================================
-- DentisAI · 20261017000400 account model v2 (ADR 017) — part 4/6: sync_events
-- =============================================================================
-- Contract changes of public.sync_events (envelope, idempotency and event_id
-- semantics are UNCHANGED, see 0500):
--   * new type screening_requested       (child accounts only)
--       payload keys: request_id (uuid, required), child_id (uuid, required),
--                     reason_code (required: toothache | sensitive_teeth |
--                     bleeding_gums | swelling | other), pain_level (optional,
--                     integer 0..10 or null)
--   * new type brushing_proof_registered (child accounts only)
--       payload keys (all required): proof_id (uuid), child_id (uuid),
--                     brushing_event_id (uuid, an applied brushing_recorded of
--                     the same child), size_bytes (integer >= 1), duration_s
--                     (integer >= 1), mime (video/mp4 | video/quicktime).
--       The server builds object_path = <owner_id>/<child_id>/<proof_id>.(mp4|mov)
--       (owner_id = the guardian = child_account.guardian_id); upload the file
--       there AFTER this event is applied.
--   * screening_recorded accepts the optional key request_id (uuid or null): a
--     request of the SAME child; when it is pending/seen and not expired it
--     becomes done (responded_at, screening_id set). Unknown / other child's
--     request -> forbidden.
--   * consent_changed accepts consent_type 'd'.
--   * Child accounts may send ONLY brushing_recorded, screening_requested and
--     brushing_proof_registered, ONLY for their own child, ONLY while enabled;
--     everything else -> forbidden. Guardians/teens/kader cannot send the two
--     new types (forbidden) and keep every old type.
--   * new rejection code limit_exceeded (terminal, stored): daily request /
--     proof limits, proof size or duration above app_setting maxima.
-- Gates per new type, in order: forbidden -> deletion_pending ->
-- feature_disabled (proof) -> consent_required ((b); proof also (d)) ->
-- limit_exceeded -> schema_invalid (unknown brushing_event_id).
-- Re-runnable.

-- Shared gate for data events: access (owner/kader, or the child account for
-- its own child), pending deletion, consent (b).
create or replace function private.sync_gate_b(p_child uuid)
returns text
language plpgsql
stable
security invoker
set search_path = ''
as $$
begin
  if private.is_child_account() then
    if not private.child_can_access(p_child) then
      return 'forbidden';
    end if;
  elsif not private.can_access_child(p_child) then
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

-- Marks a pending/seen, unexpired request of the child as answered by a
-- screening. Definer (clients have no UPDATE on screening_request); re-checks
-- that the caller may access the child.
create or replace function private.complete_screening_request(p_request uuid, p_child uuid, p_screening uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  if p_request is null or not private.can_access_child(p_child) then
    return;
  end if;
  update public.screening_request r
     set status = 'done', responded_at = now(), screening_id = p_screening
   where r.id = p_request and r.child_id = p_child
     and r.status in ('pending', 'seen') and r.expires_at > now();
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
  v_id uuid := private.try_uuid(p ->> 'screening_id');
  v_req uuid;
begin
  if not private.jsonb_only_keys(p, array['screening_id', 'child_id', 'screened_at', 'model_version',
                                          'rules_version', 'level', 'red_flag', 'findings', 'request_id'])
     or v_id is null
     or p ->> 'screened_at' is null or p ->> 'level' is null then
    return 'schema_invalid';
  end if;
  if p ? 'request_id' and jsonb_typeof(p -> 'request_id') <> 'null' then
    v_req := private.try_uuid(p ->> 'request_id');
    if v_req is null or jsonb_typeof(p -> 'request_id') <> 'string' then
      return 'schema_invalid';
    end if;
  end if;
  v_code := private.sync_gate_b(p_child);
  if v_code is not null then
    return v_code;
  end if;
  insert into public.screening (id, child_id, recorded_by, screened_at, model_version, rules_version,
                                level, red_flag, findings, request_id)
  values (v_id, p_child, (select auth.uid()),
          private.sane_ts(p ->> 'screened_at'), p ->> 'model_version', p ->> 'rules_version',
          (p ->> 'level')::public.triage_level, coalesce((p ->> 'red_flag')::boolean, false),
          coalesce(p -> 'findings', '[]'::jsonb), v_req);
  if v_req is not null then
    perform private.complete_screening_request(v_req, p_child, v_id);
  end if;
  return null;
end
$$;

create or replace function private.sync_screening_requested(p_child uuid, p jsonb)
returns text
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_id uuid := private.try_uuid(p ->> 'request_id');
  v_reason text := p ->> 'reason_code';
  v_pain smallint;
  v_code text;
begin
  if not private.jsonb_only_keys(p, array['request_id', 'child_id', 'reason_code', 'pain_level'])
     or v_id is null
     or jsonb_typeof(p -> 'reason_code') is distinct from 'string'
     or v_reason not in ('toothache', 'sensitive_teeth', 'bleeding_gums', 'swelling', 'other') then
    return 'schema_invalid';
  end if;
  if p ? 'pain_level' and jsonb_typeof(p -> 'pain_level') <> 'null' then
    if jsonb_typeof(p -> 'pain_level') <> 'number' or (p ->> 'pain_level') !~ '^[0-9]{1,2}$' then
      return 'schema_invalid';
    end if;
    v_pain := (p ->> 'pain_level')::smallint;
    if v_pain > 10 then
      return 'schema_invalid';
    end if;
  end if;
  if not private.is_child_account() then
    return 'forbidden';                     -- requests come from child accounts only
  end if;
  v_code := private.sync_gate_b(p_child);
  if v_code is not null then
    return v_code;
  end if;
  if (select count(*) from public.screening_request r
      where r.child_id = p_child and r.created_at >= private.wib_day_start())
     >= private.setting_int('request_max_per_child_per_day', 5) then
    return 'limit_exceeded';
  end if;
  insert into public.screening_request (id, child_id, requested_by, reason_code, pain_level)
  values (v_id, p_child, (select auth.uid()), v_reason, v_pain);
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
  if not private.is_child_account() or not private.child_can_access(p_child) then
    return 'forbidden';                     -- proofs come from the child's own account only
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

-- ---------------------------------------------------------------------------
-- One event: envelope validation, idempotency, dispatch, error mapping.
-- (redefined: new types, child-account restriction, limit_exceeded)
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
         'brushing_recorded', 'referral_created', 'image_upload_registered',
         'screening_requested', 'brushing_proof_registered') then
      v_code := 'unknown_event_type';
    elsif jsonb_typeof(v_payload) is distinct from 'object' or octet_length(v_payload::text) > 8192 then
      v_code := 'schema_invalid';
    else
      v_type := v_type_txt::public.sync_event_type;
      v_child := private.try_uuid(v_payload ->> 'child_id');
      if v_child is null then
        v_code := 'schema_invalid';
      elsif private.is_child_account()
            and v_type_txt not in ('brushing_recorded', 'screening_requested', 'brushing_proof_registered') then
        v_code := 'forbidden';
      else
        v_code := case v_type_txt
          when 'consent_changed'           then private.sync_consent_changed(v_child, v_payload)
          when 'child_upserted'            then private.sync_child_upserted(v_child, v_payload)
          when 'screening_recorded'        then private.sync_screening_recorded(v_child, v_payload)
          when 'brushing_recorded'         then private.sync_brushing_recorded(v_child, v_payload)
          when 'referral_created'          then private.sync_referral_created(v_child, v_payload)
          when 'image_upload_registered'   then private.sync_image_upload_registered(v_child, v_payload)
          when 'screening_requested'       then private.sync_screening_requested(v_child, v_payload)
          when 'brushing_proof_registered' then private.sync_brushing_proof_registered(v_child, v_payload)
        end;
      end if;
    end if;
  exception
    when insufficient_privilege then v_code := 'forbidden';            -- RLS backstop
    when unique_violation then v_code := 'entity_conflict';
    when foreign_key_violation then v_code := 'forbidden';
    when sqlstate 'DA429' then v_code := 'limit_exceeded';             -- trigger backstop
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

comment on function public.sync_events(jsonb) is
  'Idempotent outbox ingestion (FR-100). Contract: migration 0500 header + ADR 017 additions in 20261017000400 (screening_requested, brushing_proof_registered, request_id, consent d, limit_exceeded).';

revoke all on function
  private.complete_screening_request(uuid, uuid, uuid),
  private.sync_screening_requested(uuid, jsonb),
  private.sync_brushing_proof_registered(uuid, jsonb)
from public, anon;

grant execute on function
  private.complete_screening_request(uuid, uuid, uuid),
  private.sync_screening_requested(uuid, jsonb),
  private.sync_brushing_proof_registered(uuid, jsonb)
to authenticated, service_role;
