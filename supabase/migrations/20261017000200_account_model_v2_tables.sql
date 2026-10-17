-- =============================================================================
-- DentisAI · 20261017000200 account model v2 (ADR 017) — part 2/6: tables
-- =============================================================================
-- New columns / tables (binding names, ADR 017 §3):
--   guardian.account_type      guardian | teen (child accounts get NO guardian row)
--   child_profile.is_self      the ONE self profile of a teen account
--   child_account              login of a child (created by the guardian through the
--                              Edge Function child-accounts, service_role only);
--                              holds the username = login handle, NOT identity.
--                              The username is stored here and nowhere else.
--   screening_request          child -> guardian "please screen me" (codes only)
--   brushing_proof             metadata of a short brushing video (bucket
--                              brushing_proofs, consent d, 7-day retention)
--   child_login_attempt        service-only throttling ledger of child-login
--   screening.request_id       optional link screening -> screening_request
-- Data minimisation kept: no free text anywhere (reason codes, mime allow-list,
-- regex-checked paths), no names, no storage URLs. RLS ENABLED + FORCED below;
-- policies in part 3. Re-runnable.

-- -----------------------------------------------------------------------------
-- guardian.account_type
-- -----------------------------------------------------------------------------
alter table public.guardian
  add column if not exists account_type public.account_type not null default 'guardian';

do $$ begin
  alter table public.guardian
    add constraint guardian_account_type_not_child check (account_type in ('guardian', 'teen'));
exception when duplicate_object then null; end $$;

comment on table public.guardian is
  'Account profile of a guardian (orang tua/kader) or teen (ADR 017). Intentionally holds no identity data; child accounts have no row here.';
comment on column public.guardian.account_type is
  'guardian | teen, chosen at signUp (options.data.account_type), not an authorisation boundary. child never appears here.';

-- -----------------------------------------------------------------------------
-- child_profile.is_self
-- -----------------------------------------------------------------------------
alter table public.child_profile
  add column if not exists is_self boolean not null default false;

create unique index if not exists child_profile_one_self_per_owner
  on public.child_profile (owner_id) where is_self;

comment on table public.child_profile is
  'Pseudonymous child (UUID). Year+month of birth only; no name/NIK/exact DOB columns by design. is_self = the single self profile of a teen account (ADR 017).';
comment on column public.child_profile.is_self is
  'true only for the self profile of a teen account (guard trigger); a teen owns no other profile.';

-- -----------------------------------------------------------------------------
-- child_account: one Supabase Auth user per child (synthetic e-mail
-- child-<child_id>@child.dentisai.invalid, created by the Edge Function with
-- service_role). Written ONLY by the signup trigger (from raw_app_meta_data) and
-- service_role; clients read their own rows.
-- -----------------------------------------------------------------------------
create table if not exists public.child_account (
  user_id        uuid primary key references auth.users (id) on delete cascade,
  child_id       uuid not null unique references public.child_profile (id) on delete cascade,
  guardian_id    uuid not null references auth.users (id) on delete cascade,
  username       text not null check (username ~ '^[a-z0-9][a-z0-9._-]{2,19}$'),
  disabled_at    timestamptz,
  created_at     timestamptz not null default now(),
  last_login_at  timestamptz,
  unique (guardian_id, username)
);
comment on table public.child_account is
  'Login of a child account (ADR 017 §2.2). username = login handle (nickname), unique per guardian; it is stored ONLY here (never copied to other tables, audit or history). disabled_at set -> all access revoked at once (RLS).';
comment on column public.child_account.username is
  'Login handle, lowercase [a-z0-9._-], 3..20 chars. A nickname, not the real name (UI must say so; the server cannot verify).';

-- -----------------------------------------------------------------------------
-- screening_request: child asks the guardian for a screening. Codes only.
-- id = client-generated UUID (from the outbox event payload).
-- -----------------------------------------------------------------------------
create table if not exists public.screening_request (
  id            uuid primary key,
  child_id      uuid not null references public.child_profile (id) on delete cascade,
  requested_by  uuid references auth.users (id) on delete set null,
  reason_code   text not null
                check (reason_code in ('toothache', 'sensitive_teeth', 'bleeding_gums', 'swelling', 'other')),
  pain_level    smallint check (pain_level between 0 and 10),
  status        public.request_status not null default 'pending',
  created_at    timestamptz not null default now(),
  seen_at       timestamptz,
  responded_at  timestamptz,
  screening_id  uuid references public.screening (id) on delete set null,
  expires_at    timestamptz not null,
  constraint screening_request_expiry_after_create check (expires_at > created_at),
  constraint screening_request_status_timestamps check (
    (status <> 'seen' or seen_at is not null)
    and (status not in ('done', 'cancelled') or responded_at is not null)
  )
);
create index if not exists screening_request_child_idx on public.screening_request (child_id, created_at desc);
create index if not exists screening_request_open_idx on public.screening_request (status, expires_at);
comment on table public.screening_request is
  'Child -> guardian screening request (ADR 017 §2.4). reason_code is a code, no free text; swelling is only a marker (no server-side triage).';

alter table public.screening
  add column if not exists request_id uuid references public.screening_request (id) on delete set null;
create index if not exists screening_request_id_idx on public.screening (request_id);
comment on column public.screening.request_id is
  'Optional screening_request answered by this screening (ADR 017); recording it marks a pending/seen request done.';

-- -----------------------------------------------------------------------------
-- brushing_proof: metadata of one short brushing clip. The video lives in the
-- PRIVATE bucket brushing_proofs at '<guardian_id>/<child_id>/<proof_id>.(mp4|mov)'
-- (path built by the server). Never used for training, never shared with
-- kader/facility/admin. Retention proof_retention_days (default 7).
-- -----------------------------------------------------------------------------
create table if not exists public.brushing_proof (
  id                 uuid primary key,
  child_id           uuid not null references public.child_profile (id) on delete cascade,
  brushing_event_id  uuid not null unique references public.brushing_event (id) on delete cascade,
  uploaded_by        uuid references auth.users (id) on delete set null,
  object_path        text not null unique
                     check (object_path ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.(mp4|mov)$'),
  mime               text not null check (mime in ('video/mp4', 'video/quicktime')),
  size_bytes         integer not null check (size_bytes between 1 and 52428800),
  duration_s         integer not null check (duration_s between 1 and 1800),
  status             public.proof_status not null default 'uploaded',
  uploaded_at        timestamptz not null default now(),
  expires_at         timestamptz not null,
  viewed_at          timestamptz,
  viewed_by          uuid references auth.users (id) on delete set null,
  deleted_at         timestamptz,
  object_removed_at  timestamptz,
  constraint brushing_proof_expiry_after_upload check (expires_at > uploaded_at),
  constraint brushing_proof_ext_matches_mime check (
    (mime = 'video/mp4' and object_path like '%.mp4')
    or (mime = 'video/quicktime' and object_path like '%.mov')),
  constraint brushing_proof_status_timestamps check (
    (status <> 'viewed' or viewed_at is not null)
    and (status <> 'deleted' or deleted_at is not null))
);
create index if not exists brushing_proof_child_idx on public.brushing_proof (child_id, uploaded_at desc);
create index if not exists brushing_proof_sweep_idx on public.brushing_proof (object_removed_at, expires_at);
comment on table public.brushing_proof is
  'Brushing proof clip metadata (ADR 017 §2.5). Video only in bucket brushing_proofs; consent (d) + flag brushing_proof_video; 7-day retention; never for training; no access for kader/facility/admin.';
comment on column public.brushing_proof.uploaded_at is 'Registration time (the upload to Storage follows the registration).';
comment on column public.brushing_proof.object_removed_at is 'When the sweeper actually removed the Storage object; the row is purged afterwards.';

-- -----------------------------------------------------------------------------
-- child_login_attempt: throttling ledger for Edge Function child-login.
-- key_hash = hash(parent e-mail | ip) computed by the Edge Function (hex SHA-256
-- recommended); the charset forbids '@' and '.', so a raw e-mail/IP cannot be
-- stored. window_start = bucket start (sliding window made of small buckets).
-- service_role only.
-- -----------------------------------------------------------------------------
create table if not exists public.child_login_attempt (
  key_hash      text not null check (key_hash ~ '^[0-9A-Za-z_+/=-]{32,128}$'),
  window_start  timestamptz not null,
  attempts      integer not null default 0 check (attempts >= 0),
  primary key (key_hash, window_start)
);
comment on table public.child_login_attempt is
  'service_role only. Failed child-login attempts per hashed (parent e-mail | ip) and time bucket. No raw e-mail/IP.';

-- -----------------------------------------------------------------------------
-- Truthful table comments where ADR 017 relaxed the original rules (0200).
-- -----------------------------------------------------------------------------
comment on table public.brushing_event is
  'Brushing session: duration + skor semangat. The row never holds video/frames; an optional short proof clip (ADR 017) is referenced by brushing_proof and stored in bucket brushing_proofs for at most proof_retention_days.';

-- -----------------------------------------------------------------------------
-- Settings (ADR 017 ★) and feature flag
-- -----------------------------------------------------------------------------
insert into public.app_setting (key, value, description) values
  ('teen_min_age', '10', 'ADR 017: usia minimum akun remaja (tahun, dari tahun-bulan lahir profil diri).'),
  ('teen_max_age', '19', 'ADR 017: usia maksimum akun remaja (tahun; definisi WHO adolescent).'),
  ('max_child_accounts_per_guardian', '6', 'ADR 017: jumlah maksimum akun anak per orang tua.'),
  ('child_login_max_attempts', '8', 'ADR 017: percobaan login anak gagal maksimum per jendela per hash(email orang tua | ip).'),
  ('child_login_window_s', '900', 'ADR 017: panjang jendela pembatasan login anak (detik).'),
  ('streak_min_duration_s', '60', 'ADR 017: durasi sikat gigi minimum (detik) agar satu hari terhitung untuk streak.'),
  ('request_max_per_child_per_day', '5', 'ADR 017: permintaan skrining maksimum per anak per hari kalender WIB.'),
  ('request_expiry_days', '14', 'ADR 017: permintaan skrining kedaluwarsa setelah N hari.'),
  ('proof_max_bytes', '10485760', 'ADR 017: ukuran maksimum video bukti (byte); sama dengan batas bucket brushing_proofs.'),
  ('proof_max_duration_s', '180', 'ADR 017: durasi maksimum video bukti (detik).'),
  ('proof_max_per_child_per_day', '3', 'ADR 017: video bukti maksimum per anak per hari kalender WIB.'),
  ('proof_retention_days', '7', 'ADR 017: retensi video bukti (hari); berkas dan baris dihapus otomatis.')
on conflict (key) do nothing;

insert into public.feature_flag (key, enabled, description) values
  ('brushing_proof_video', true,
   'ADR 017: bukti sikat gigi berupa video dari akun anak ke orang tua. Tetap butuh consent (d) per anak.')
on conflict (key) do nothing;

-- -----------------------------------------------------------------------------
-- RLS (ENABLED + FORCED, default deny) and baseline grants. Policies: part 3.
-- -----------------------------------------------------------------------------
alter table public.child_account enable row level security;
alter table public.child_account force row level security;
alter table public.screening_request enable row level security;
alter table public.screening_request force row level security;
alter table public.brushing_proof enable row level security;
alter table public.brushing_proof force row level security;
alter table public.child_login_attempt enable row level security;
alter table public.child_login_attempt force row level security;

revoke all on public.child_account, public.screening_request, public.brushing_proof, public.child_login_attempt
  from public, anon, authenticated;
grant select, insert, update, delete
  on public.child_account, public.screening_request, public.brushing_proof, public.child_login_attempt
  to service_role;

-- Column-scoped client grants (rows decided by policies in part 3).
grant select on public.child_account to authenticated;                    -- no client writes
grant select on public.screening_request to authenticated;
grant insert (id, child_id, requested_by, reason_code, pain_level) on public.screening_request to authenticated;
grant select on public.brushing_proof to authenticated;
grant insert (id, child_id, brushing_event_id, uploaded_by, object_path, mime, size_bytes, duration_s)
  on public.brushing_proof to authenticated;
grant insert (request_id) on public.screening to authenticated;
-- child_login_attempt: nothing for clients.
