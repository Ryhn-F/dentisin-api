-- =============================================================================
-- DentisAI · 0200 core tables (PRD §9.3, backend/CLAUDE.md §6)
-- =============================================================================
-- Data minimisation rules enforced by schema:
--   * children are pseudonymous UUIDs; there is NO column for name, NIK, exact
--     date of birth, address, school-assigned student number, photo or free text;
--   * birth date is year + month only (CHECK-constrained);
--   * free-form strings are replaced by constrained codes (regex CHECKs) so that
--     names cannot be smuggled into "label" or "allergy" fields;
--   * screening rows never contain images (photos only via opt-in image_upload).
-- RLS is enabled + forced for every table in 0400_rls_policies.sql.

-- Validators used by CHECK constraints ---------------------------------------

-- Pattern for semver-like model/rules versions, e.g. 1.2.0, 0.1.0-rc.1
create or replace function private.is_version_tag(p text)
returns boolean
language sql
immutable
set search_path = ''
as $$
  select p ~ '^[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}([-+][0-9A-Za-z.-]{1,32})?$'
$$;

-- Array of short lowercase codes (allergy codes, reason codes). No free text.
create or replace function private.is_code_array(p text[], p_max int)
returns boolean
language sql
immutable
set search_path = ''
as $$
  select p is not null
     and cardinality(p) <= p_max
     and array_position(p, null) is null
     and (cardinality(p) = 0
          or array_to_string(p, ',') ~ '^[a-z][a-z0-9_]{1,39}(,[a-z][a-z0-9_]{1,39})*$')
$$;

-- JSON number within [0, 1] (no cast is attempted on non-numbers).
create or replace function private.is_unit_number(p jsonb)
returns boolean
language sql
immutable
set search_path = ''
as $$
  select coalesce(
    case when jsonb_typeof(p) = 'number' then (p #>> '{}')::numeric between 0 and 1 end,
    false)
$$;

-- Detector findings: array (<= 50) of objects with ONLY these keys:
--   class (code), confidence (0..1), rel_area (0..1), box ([x,y,w,h] in 0..1)
create or replace function private.findings_valid(p jsonb)
returns boolean
language plpgsql
immutable
set search_path = ''
as $$
declare
  item jsonb;
  v jsonb;
  k text;
begin
  if p is null or jsonb_typeof(p) <> 'array' or jsonb_array_length(p) > 50 then
    return false;
  end if;
  for item in select e from jsonb_array_elements(p) as t(e) loop
    if jsonb_typeof(item) <> 'object' then
      return false;
    end if;
    for k in select jsonb_object_keys(item) loop
      if k not in ('class', 'confidence', 'rel_area', 'box') then
        return false;
      end if;
    end loop;
    if jsonb_typeof(item -> 'class') is distinct from 'string'
       or (item ->> 'class') !~ '^[a-z][a-z0-9_]{1,39}$' then
      return false;
    end if;
    if item ? 'confidence' and not private.is_unit_number(item -> 'confidence') then
      return false;
    end if;
    if item ? 'rel_area' and not private.is_unit_number(item -> 'rel_area') then
      return false;
    end if;
    if item ? 'box' then
      if jsonb_typeof(item -> 'box') <> 'array' or jsonb_array_length(item -> 'box') <> 4 then
        return false;
      end if;
      for v in select e from jsonb_array_elements(item -> 'box') as b(e) loop
        if not private.is_unit_number(v) then
          return false;
        end if;
      end loop;
    end if;
  end loop;
  return true;
end
$$;

-- Flat, PII-free detail object for audit_log / proofs: <= 20 keys; values are
-- numbers, booleans, null, or short lowercase codes that are not UUID-shaped.
create or replace function private.is_safe_detail(p jsonb)
returns boolean
language plpgsql
immutable
set search_path = ''
as $$
declare
  e record;
  n int := 0;
  s text;
begin
  if p is null or jsonb_typeof(p) <> 'object' then
    return false;
  end if;
  for e in select key, value from jsonb_each(p) loop
    n := n + 1;
    if n > 20 or e.key !~ '^[a-z][a-z0-9_]{0,39}$' then
      return false;
    end if;
    if jsonb_typeof(e.value) not in ('number', 'boolean', 'null', 'string') then
      return false;
    end if;
    if jsonb_typeof(e.value) = 'string' then
      s := e.value #>> '{}';
      if s !~ '^[a-z0-9_.:-]{0,64}$' or s ~ '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}' then
        return false;
      end if;
    end if;
  end loop;
  return true;
end
$$;

-- Facility / puskesmas code (non-personal), e.g. PKM-3201-01
-- (kept inline in CHECKs below: '^[A-Z0-9][A-Z0-9-]{2,31}$')

-- -----------------------------------------------------------------------------
-- guardian: one row per account (parent OR kader). No name, phone or address;
-- the e-mail lives only in auth.users (managed by Supabase Auth).
-- -----------------------------------------------------------------------------
create table if not exists public.guardian (
  id          uuid primary key references auth.users (id) on delete cascade,
  locale      text not null default 'id' check (locale in ('id')),
  created_at  timestamptz not null default now()
);
comment on table public.guardian is 'Account profile (orang tua/kader). Intentionally holds no identity data.';

-- -----------------------------------------------------------------------------
-- user_role: authorisation source of truth. NOT writable by clients; only the
-- signup trigger (always ''guardian''), admin RPCs and service_role write here.
-- -----------------------------------------------------------------------------
create table if not exists public.user_role (
  user_id        uuid not null references auth.users (id) on delete cascade,
  role           public.app_role not null,
  facility_code  text check (facility_code ~ '^[A-Z0-9][A-Z0-9-]{2,31}$'),
  granted_by     uuid references auth.users (id) on delete set null,
  granted_at     timestamptz not null default now(),
  primary key (user_id, role),
  constraint facility_viewer_needs_scope check (role <> 'facility_viewer' or facility_code is not null)
);
comment on table public.user_role is 'Roles per account. facility_code scopes kader rosters and facility_viewer aggregates.';

-- -----------------------------------------------------------------------------
-- child_profile: pseudonymous child. id is generated on the device (UUID v4).
-- -----------------------------------------------------------------------------
create table if not exists public.child_profile (
  id           uuid primary key,
  owner_id     uuid not null references auth.users (id) on delete cascade,
  birth_year   smallint check (birth_year between 2005 and 2035),
  birth_month  smallint check (birth_month between 1 and 12),
  allergies    text[] not null default '{}' check (private.is_code_array(allergies, 10)),
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  constraint birth_month_requires_year check (birth_month is null or birth_year is not null)
);
create index if not exists child_profile_owner_idx on public.child_profile (owner_id);
comment on table public.child_profile is 'Pseudonymous child (UUID). Year+month of birth only; no name/NIK/exact DOB columns by design.';
comment on column public.child_profile.allergies is 'Allergy CODES (e.g. sls, mint, fluoride_none) used by the recommender; never free text.';

-- -----------------------------------------------------------------------------
-- consent: granular (a/b/c), versioned text, withdrawable. Max one ACTIVE row
-- per (child, type). Rows are never deleted except by right-to-delete.
-- -----------------------------------------------------------------------------
create table if not exists public.consent (
  id            uuid primary key default gen_random_uuid(),
  child_id      uuid not null references public.child_profile (id) on delete cascade,
  granted_by    uuid references auth.users (id) on delete set null,
  consent_type  public.consent_type not null,
  text_version  text not null check (text_version ~ '^[A-Za-z0-9._-]{1,32}$'),
  method        public.consent_method not null default 'in_app',
  granted_at    timestamptz not null,
  withdrawn_at  timestamptz,
  created_at    timestamptz not null default now(),
  constraint consent_withdrawn_after_granted check (withdrawn_at is null or withdrawn_at >= granted_at)
);
create unique index if not exists consent_one_active_per_type
  on public.consent (child_id, consent_type) where withdrawn_at is null;

-- -----------------------------------------------------------------------------
-- screening: triage result + detector findings. NEVER an image.
-- id = client-generated screening UUID (from the outbox event payload).
-- -----------------------------------------------------------------------------
create table if not exists public.screening (
  id             uuid primary key,
  child_id       uuid not null references public.child_profile (id) on delete cascade,
  recorded_by    uuid references auth.users (id) on delete set null,
  screened_at    timestamptz not null,
  model_version  text not null check (private.is_version_tag(model_version)),
  rules_version  text not null check (private.is_version_tag(rules_version)),
  level          public.triage_level not null,
  red_flag       boolean not null default false,
  findings       jsonb not null default '[]'::jsonb check (private.findings_valid(findings)),
  created_at     timestamptz not null default now()
);
create index if not exists screening_child_idx on public.screening (child_id);
create index if not exists screening_screened_at_idx on public.screening (screened_at);

-- -----------------------------------------------------------------------------
-- image_upload: metadata of an opt-in dataset photo (consent c + feature flag).
-- The photo itself lives in storage bucket dataset_photos at object_path
-- '<uploader uid>/<child id>/<upload uuid>.jpg'. TTL via expires_at.
-- -----------------------------------------------------------------------------
create table if not exists public.image_upload (
  id            uuid primary key,
  child_id      uuid not null references public.child_profile (id) on delete cascade,
  uploaded_by   uuid references auth.users (id) on delete set null,
  consent_id    uuid references public.consent (id) on delete set null,
  bucket_id     text not null default 'dataset_photos' check (bucket_id = 'dataset_photos'),
  object_path   text not null unique
                check (object_path ~ '^[0-9a-f-]{36}/[0-9a-f-]{36}/[0-9a-f-]{36}\.jpe?g$'),
  status        public.image_upload_status not null default 'uploaded',
  uploaded_at   timestamptz not null default now(),
  expires_at    timestamptz not null,
  withdrawn_at  timestamptz,
  deleted_at    timestamptz,
  constraint image_upload_expiry_after_upload check (expires_at > uploaded_at),
  constraint image_upload_status_timestamps check (
    (status <> 'withdrawn' or withdrawn_at is not null)
    and (status <> 'deleted' or deleted_at is not null)
  )
);
create index if not exists image_upload_child_idx on public.image_upload (child_id);
create index if not exists image_upload_sweep_idx on public.image_upload (status, expires_at);

-- -----------------------------------------------------------------------------
-- referral: referral letter metadata (the PDF is generated offline on device).
-- -----------------------------------------------------------------------------
create table if not exists public.referral (
  id             uuid primary key,
  child_id       uuid not null references public.child_profile (id) on delete cascade,
  screening_id   uuid references public.screening (id) on delete set null,
  created_by     uuid references auth.users (id) on delete set null,
  level          public.triage_level not null,
  reason_codes   text[] not null default '{}' check (private.is_code_array(reason_codes, 20)),
  facility_code  text check (facility_code ~ '^[A-Z0-9][A-Z0-9-]{2,31}$'),
  referred_at    timestamptz not null,
  created_at     timestamptz not null default now()
);
create index if not exists referral_child_idx on public.referral (child_id);
create index if not exists referral_screening_idx on public.referral (screening_id);

-- -----------------------------------------------------------------------------
-- brushing_event: duration + "skor semangat". No video/frames, ever.
-- -----------------------------------------------------------------------------
create table if not exists public.brushing_event (
  id            uuid primary key,
  child_id      uuid not null references public.child_profile (id) on delete cascade,
  recorded_by   uuid references auth.users (id) on delete set null,
  started_at    timestamptz not null,
  duration_s    integer not null check (duration_s between 0 and 1800),
  spirit_score  smallint check (spirit_score between 0 and 100),
  slot          public.brushing_slot not null default 'other',
  camera_mode   boolean not null default false,
  created_at    timestamptz not null default now()
);
create index if not exists brushing_event_child_idx on public.brushing_event (child_id);

-- -----------------------------------------------------------------------------
-- class_roster (+ members): kader mode. Pseudonymous member labels only
-- (e.g. "A-07"); the regex forbids names.
-- -----------------------------------------------------------------------------
create table if not exists public.class_roster (
  id             uuid primary key default gen_random_uuid(),
  kader_id       uuid not null references auth.users (id) on delete cascade,
  label          text not null check (label ~ '^[A-Za-z0-9][A-Za-z0-9 .-]{0,23}$'),
  school_year    text check (school_year ~ '^[0-9]{4}/[0-9]{4}$'),
  facility_code  text check (facility_code ~ '^[A-Z0-9][A-Z0-9-]{2,31}$'),
  created_at     timestamptz not null default now()
);
create index if not exists class_roster_kader_idx on public.class_roster (kader_id);
comment on column public.class_roster.label is 'Class label such as "Kelas 4A". Do not put names of people here.';

create table if not exists public.class_roster_member (
  roster_id     uuid not null references public.class_roster (id) on delete cascade,
  child_id      uuid not null references public.child_profile (id) on delete cascade,
  member_label  text not null check (member_label ~ '^[A-Z]{0,3}-?[0-9]{1,4}$'),
  added_at      timestamptz not null default now(),
  primary key (roster_id, child_id),
  unique (roster_id, member_label)
);
create index if not exists class_roster_member_child_idx on public.class_roster_member (child_id);

-- -----------------------------------------------------------------------------
-- deletion_request: right-to-delete with proof. subject_id is cleared when the
-- request completes; only a keyed hash (subject_hash) and counts remain.
-- -----------------------------------------------------------------------------
create table if not exists public.deletion_request (
  id                       uuid primary key default gen_random_uuid(),
  requested_by             uuid references auth.users (id) on delete set null,
  scope                    public.deletion_scope not null,
  subject_id               uuid,            -- child id or account id; NO FK on purpose (must survive the deletion), nulled on completion
  status                   public.deletion_status not null default 'pending',
  requested_at             timestamptz not null default now(),
  started_at               timestamptz,
  completed_at             timestamptz,
  attempts                 smallint not null default 0,
  executed_by              text check (executed_by in ('pg_cron', 'edge:process-deletion', 'admin')),
  subject_hash             text check (subject_hash ~ '^[0-9a-f]{64}$'),
  rows_deleted             jsonb check (rows_deleted is null or private.is_safe_detail(rows_deleted)),
  storage_objects_deleted  integer check (storage_objects_deleted >= 0),
  proof_sha256             text check (proof_sha256 ~ '^[0-9a-f]{64}$'),
  failure_code             text check (failure_code ~ '^[a-z0-9_]{1,40}$'),
  constraint deletion_subject_present check (status = 'completed' or subject_id is not null),
  constraint deletion_completed_has_proof check (
    status <> 'completed'
    or (subject_id is null and completed_at is not null and subject_hash is not null
        and rows_deleted is not null and proof_sha256 is not null)
  )
);
create index if not exists deletion_request_status_idx on public.deletion_request (status, requested_at);
create index if not exists deletion_request_requested_by_idx on public.deletion_request (requested_by);

-- -----------------------------------------------------------------------------
-- audit_log: append-only, PII-free. Actor/target are HMAC-SHA256 hashes keyed
-- with a DB-internal pepper (private.secret) — never raw ids, e-mails or names.
-- -----------------------------------------------------------------------------
create table if not exists public.audit_log (
  id            bigint generated always as identity primary key,
  occurred_at   timestamptz not null default now(),
  actor_role    text not null check (actor_role ~ '^[a-z_:.-]{1,40}$'),
  actor_hash    text check (actor_hash ~ '^[0-9a-f]{64}$'),
  action        text not null check (action ~ '^[a-z][a-z0-9_]{1,63}$'),
  target_table  text check (target_table ~ '^[a-z][a-z0-9_]{1,63}$'),
  target_hash   text check (target_hash ~ '^[0-9a-f]{64}$'),
  details       jsonb not null default '{}'::jsonb check (private.is_safe_detail(details))
);
create index if not exists audit_log_occurred_idx on public.audit_log (occurred_at);

-- -----------------------------------------------------------------------------
-- sync_event: idempotency ledger for sync_events(). Stores the payload HASH and
-- the terminal per-event result — never the payload itself.
-- -----------------------------------------------------------------------------
create table if not exists public.sync_event (
  event_id        uuid primary key,
  submitted_by    uuid references auth.users (id) on delete cascade,
  event_type      public.sync_event_type,
  child_id        uuid references public.child_profile (id) on delete cascade,
  payload_sha256  text not null check (payload_sha256 ~ '^[0-9a-f]{64}$'),
  result          jsonb not null,
  received_at     timestamptz not null default now()
);
create index if not exists sync_event_submitted_by_idx on public.sync_event (submitted_by);
create index if not exists sync_event_child_idx on public.sync_event (child_id);
create index if not exists sync_event_received_idx on public.sync_event (received_at);

-- -----------------------------------------------------------------------------
-- feature_flag / app_setting: operational switches readable by dentists/ethics.
-- -----------------------------------------------------------------------------
create table if not exists public.feature_flag (
  key          text primary key check (key ~ '^[a-z][a-z0-9_]{1,63}$'),
  enabled      boolean not null default false,
  description  text not null,
  approval_ref text check (approval_ref ~ '^[A-Za-z0-9./_-]{1,64}$'),
  updated_at   timestamptz not null default now(),
  updated_by   uuid references auth.users (id) on delete set null
);

insert into public.feature_flag (key, enabled, description) values
  ('dataset_contribution', false,
   'FR-25: unggah foto dataset (consent c). WAJIB mati sampai KEPK menyetujui; mengaktifkan butuh approval_ref.')
on conflict (key) do nothing;

create table if not exists public.app_setting (
  key          text primary key check (key ~ '^[a-z][a-z0-9_]{1,63}$'),
  value        jsonb not null,
  description  text not null,
  updated_at   timestamptz not null default now()
);

insert into public.app_setting (key, value, description) values
  ('image_upload_ttl_days', '365',
   'Retensi foto dataset (hari) sejak unggah. [PERLU VERIFIKASI] harus mengikuti protokol KEPK.'),
  ('sync_event_retention_days', '180',
   'Berapa lama buku besar idempotensi sync_event disimpan (hari).'),
  ('sync_batch_max_events', '100',
   'Jumlah maksimum event per panggilan sync_events.'),
  ('aggregate_min_cell_size', '5',
   'Ukuran sel minimum agregat facility_viewer (nilai < 5 tidak berlaku; selalu minimal 5).'),
  ('deletion_max_attempts', '5',
   'Percobaan maksimum job hak hapus sebelum status failed.')
on conflict (key) do nothing;

-- -----------------------------------------------------------------------------
-- private.secret: DB-internal pepper for audit hashes. No grants to anyone.
-- -----------------------------------------------------------------------------
create table if not exists private.secret (
  name   text primary key,
  value  bytea not null
);
insert into private.secret (name, value)
values ('audit_pepper', extensions.gen_random_bytes(32))
on conflict (name) do nothing;
revoke all on private.secret from public, anon, authenticated, service_role;
