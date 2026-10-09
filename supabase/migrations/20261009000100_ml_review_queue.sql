-- =============================================================================
-- DentisAI · 20261009000100 ML review queue + model registry (FastAPI ML/AI, ADR 005/009)
-- =============================================================================
-- Owned by the API engineer (backend/api). Additive only: new tables, helpers,
-- triggers and one service RPC; no existing object is altered.
--
--   ml_review_queue   one row per consented dataset photo queued for dentist
--                     review (reference to image_upload.id; never a photo/path)
--   ml_review_result  one row per (queue item, pseudonymous grader): decision +
--                     boxes in the canonical format of ml/detection/canonical.py
--   ml_model_registry verified, Ed25519-signed manifests (public material only)
--
-- Access: RLS ENABLED + FORCED, default deny. anon: nothing. authenticated:
-- SELECT only, and only admins (policies). Writes only through the API with
-- service_role (server-side, never in the app). Guard triggers re-check consent
-- (c), the dataset_contribution flag and photo status inside the INSERT so a
-- race with a consent withdrawal cannot slip through. No PII columns: actor
-- columns are auth account ids (as elsewhere in the schema), notes are
-- length-capped and documented as "no names".
-- Re-runnable (the local harness applies every migration twice).

-- -----------------------------------------------------------------------------
-- Validators / helpers
-- -----------------------------------------------------------------------------

-- Boxes: array (<= 100) of {cls, x1, y1, x2, y2} only; cls in the v1 classes;
-- absolute pixels with 0 <= x1 < x2 <= width, 0 <= y1 < y2 <= height, side >= 1.
create or replace function private.ml_boxes_valid(p jsonb, p_width integer, p_height integer)
returns boolean
language plpgsql
immutable
set search_path = ''
as $$
declare
  item jsonb;
  k text;
  x1 numeric; y1 numeric; x2 numeric; y2 numeric;
begin
  if p is null or jsonb_typeof(p) <> 'array' or jsonb_array_length(p) > 100
     or p_width is null or p_height is null then
    return false;
  end if;
  for item in select e from jsonb_array_elements(p) as t(e) loop
    if jsonb_typeof(item) <> 'object' then
      return false;
    end if;
    for k in select jsonb_object_keys(item) loop
      if k not in ('cls', 'x1', 'y1', 'x2', 'y2') then
        return false;
      end if;
    end loop;
    if (item ->> 'cls') is null
       or (item ->> 'cls') not in ('caries_cavitated', 'caries_noncavitated') then
      return false;
    end if;
    if jsonb_typeof(item -> 'x1') is distinct from 'number'
       or jsonb_typeof(item -> 'y1') is distinct from 'number'
       or jsonb_typeof(item -> 'x2') is distinct from 'number'
       or jsonb_typeof(item -> 'y2') is distinct from 'number' then
      return false;
    end if;
    x1 := (item ->> 'x1')::numeric; y1 := (item ->> 'y1')::numeric;
    x2 := (item ->> 'x2')::numeric; y2 := (item ->> 'y2')::numeric;
    if x1 < 0 or y1 < 0 or x2 > p_width or y2 > p_height
       or x2 - x1 < 1 or y2 - y1 < 1 then
      return false;
    end if;
  end loop;
  return true;
end
$$;

-- Photo may be (still) used for review: flag ON, upload 'uploaded' and not
-- expired, ACTIVE consent (c), no pending deletion for the child.
create or replace function private.ml_image_eligible(p_image uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.image_upload u
    where u.id = p_image
      and u.status = 'uploaded'
      and u.expires_at > now()
      and private.flag_enabled('dataset_contribution')
      and private.has_active_consent(u.child_id, 'c'::public.consent_type)
      and not exists (
        select 1 from public.deletion_request d
        where d.status in ('pending', 'processing') and d.scope = 'child' and d.subject_id = u.child_id)
  )
$$;

-- -----------------------------------------------------------------------------
-- Tables
-- -----------------------------------------------------------------------------
create table if not exists public.ml_review_queue (
  id                uuid primary key default gen_random_uuid(),
  image_upload_id   uuid not null unique references public.image_upload (id) on delete cascade,
  status            text not null default 'pending'
                    check (status in ('pending', 'completed', 'cancelled')),
  required_reviews  smallint not null default 2 check (required_reviews between 1 and 5),
  review_count      smallint not null default 0 check (review_count >= 0),
  enqueued_by       uuid references auth.users (id) on delete set null,
  enqueued_at       timestamptz not null default now(),
  completed_at      timestamptz,
  constraint ml_review_queue_completed_ts check ((status = 'completed') = (completed_at is not null))
);
create index if not exists ml_review_queue_pending_idx
  on public.ml_review_queue (enqueued_at, id) where status = 'pending';
comment on table public.ml_review_queue is
  'Dentist review queue of consented dataset photos (reference only). Unique image_upload_id = idempotent enqueue.';

create table if not exists public.ml_review_result (
  id                uuid primary key default gen_random_uuid(),
  queue_id          uuid not null references public.ml_review_queue (id) on delete cascade,
  reviewer_id       uuid references auth.users (id) on delete set null,
  grader_pseudonym  text not null check (grader_pseudonym ~ '^[A-Za-z0-9][A-Za-z0-9_-]{1,31}$'),
  decision          text not null check (decision in ('annotated', 'no_finding', 'ungradable')),
  image_width       integer not null check (image_width between 1 and 20000),
  image_height      integer not null check (image_height between 1 and 20000),
  boxes             jsonb not null default '[]'::jsonb,
  notes             text check (notes is null or char_length(notes) <= 500),
  created_at        timestamptz not null default now(),
  unique (queue_id, grader_pseudonym),
  constraint ml_review_result_boxes_valid check (private.ml_boxes_valid(boxes, image_width, image_height)),
  constraint ml_review_result_boxes_match_decision check (
    (decision = 'annotated') = (jsonb_array_length(boxes) > 0))
);
create index if not exists ml_review_result_queue_idx on public.ml_review_result (queue_id);
comment on column public.ml_review_result.notes is 'Short clinical note (<= 500 chars). Never names or identifiers.';
comment on column public.ml_review_result.boxes is
  'dentis-canonical-v1 boxes: [{cls, x1, y1, x2, y2}] absolute pixels of the original image.';

create table if not exists public.ml_model_registry (
  id                   uuid primary key default gen_random_uuid(),
  manifest_sha256      text not null unique check (manifest_sha256 ~ '^[0-9a-f]{64}$'),
  sequence             bigint not null unique check (sequence between 1 and 9007199254740991),
  schema_version       integer not null check (schema_version >= 1),
  channel              text not null check (channel in ('production', 'internal-test')),
  signer_key_id        text not null check (signer_key_id ~ '^[0-9a-f]{16}$'),
  min_app_version      text not null check (min_app_version ~ '^[0-9]{1,9}\.[0-9]{1,9}\.[0-9]{1,9}$'),
  model_version        text not null check (model_version ~ '^[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}$'),
  model_sha256         text not null check (model_sha256 ~ '^[0-9a-f]{64}$'),
  model_license        text not null check (char_length(model_license) between 1 and 256),
  rules_version        text not null check (rules_version ~ '^[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}$'),
  recommender_version  text not null check (recommender_version ~ '^[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}$'),
  manifest_created_at  timestamptz not null,
  expires_at           timestamptz,
  manifest_canonical   text not null check (octet_length(manifest_canonical) <= 16384),
  manifest_sig         text not null check (octet_length(manifest_sig) <= 512),
  registered_by        uuid references auth.users (id) on delete set null,
  registered_at        timestamptz not null default now()
);
comment on table public.ml_model_registry is
  'Signed model/rules manifests verified by the API (Ed25519, verify-only; private key stays offline).';

-- -----------------------------------------------------------------------------
-- Guard triggers (server-controlled columns + eligibility, race-safe)
-- -----------------------------------------------------------------------------
create or replace function private.ml_review_queue_before_insert()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if not private.ml_image_eligible(new.image_upload_id) then
    raise exception 'ml_review_not_eligible' using errcode = 'P0001';
  end if;
  new.status := 'pending';
  new.review_count := 0;
  new.enqueued_at := now();
  new.completed_at := null;
  return new;
end
$$;

drop trigger if exists ml_review_queue_before_insert on public.ml_review_queue;
create trigger ml_review_queue_before_insert
  before insert on public.ml_review_queue
  for each row execute function private.ml_review_queue_before_insert();

create or replace function private.ml_review_result_before_insert()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  q public.ml_review_queue%rowtype;
begin
  select * into q from public.ml_review_queue where id = new.queue_id for update;
  if not found or q.status <> 'pending' then
    raise exception 'ml_review_queue_closed' using errcode = 'P0001';
  end if;
  if not private.ml_image_eligible(q.image_upload_id) then
    raise exception 'ml_review_not_eligible' using errcode = 'P0001';
  end if;
  new.created_at := now();
  return new;
end
$$;

drop trigger if exists ml_review_result_before_insert on public.ml_review_result;
create trigger ml_review_result_before_insert
  before insert on public.ml_review_result
  for each row execute function private.ml_review_result_before_insert();

create or replace function private.ml_review_result_after_insert()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  update public.ml_review_queue q
     set review_count = q.review_count + 1,
         status = case when q.review_count + 1 >= q.required_reviews then 'completed' else q.status end,
         completed_at = case when q.review_count + 1 >= q.required_reviews then now() else q.completed_at end
   where q.id = new.queue_id;
  return new;
end
$$;

drop trigger if exists ml_review_result_after_insert on public.ml_review_result;
create trigger ml_review_result_after_insert
  after insert on public.ml_review_result
  for each row execute function private.ml_review_result_after_insert();

-- Review results and registry rows are append-only (corrections = new review /
-- new manifest version). The only permitted UPDATE is the FK action
-- "on delete set null" of the actor column (TG_ARGV[0]) when an account is
-- deleted; FK cascades from right-to-delete still remove rows.
create or replace function private.ml_append_only_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_col text := tg_argv[0];
begin
  if (to_jsonb(new) - v_col) = (to_jsonb(old) - v_col)
     and (to_jsonb(new) -> v_col) = 'null'::jsonb then
    return new;
  end if;
  raise exception 'append_only_table' using errcode = '42501';
end
$$;

drop trigger if exists ml_review_result_no_update on public.ml_review_result;
create trigger ml_review_result_no_update
  before update on public.ml_review_result
  for each row execute function private.ml_append_only_guard('reviewer_id');

drop trigger if exists ml_model_registry_no_update on public.ml_model_registry;
create trigger ml_model_registry_no_update
  before update on public.ml_model_registry
  for each row execute function private.ml_append_only_guard('registered_by');

-- -----------------------------------------------------------------------------
-- Audit RPC for the API (service_role only). Hashes ids with the DB pepper.
-- -----------------------------------------------------------------------------
create or replace function public.service_ml_audit(
  p_actor uuid,
  p_action text,
  p_target_table text default null,
  p_target uuid default null,
  p_details jsonb default '{}'::jsonb
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  perform private.assert_service_role();
  if p_action is null or p_action !~ '^ml_[a-z0-9_]{1,60}$' then
    raise exception 'invalid_action' using errcode = '22023';
  end if;
  if p_target_table is not null and p_target_table !~ '^(ml_[a-z0-9_]{1,60}|image_upload)$' then
    raise exception 'invalid_target_table' using errcode = '22023';
  end if;
  insert into public.audit_log (actor_role, actor_hash, action, target_table, target_hash, details)
  values ('ml_api', private.hash_ref(p_actor), p_action, p_target_table,
          private.hash_ref(p_target), coalesce(p_details, '{}'::jsonb));
end
$$;

-- -----------------------------------------------------------------------------
-- RLS (enabled + forced, default deny) and explicit grants
-- -----------------------------------------------------------------------------
alter table public.ml_review_queue enable row level security;
alter table public.ml_review_queue force row level security;
alter table public.ml_review_result enable row level security;
alter table public.ml_review_result force row level security;
alter table public.ml_model_registry enable row level security;
alter table public.ml_model_registry force row level security;

revoke all on public.ml_review_queue, public.ml_review_result, public.ml_model_registry
  from public, anon, authenticated;
grant select, insert, update, delete
  on public.ml_review_queue, public.ml_review_result, public.ml_model_registry to service_role;
grant select on public.ml_review_queue, public.ml_review_result, public.ml_model_registry
  to authenticated;                                            -- admin rows only (policies)

drop policy if exists ml_review_queue_admin_select on public.ml_review_queue;
create policy ml_review_queue_admin_select on public.ml_review_queue
  for select to authenticated using (private.is_admin());

drop policy if exists ml_review_result_admin_select on public.ml_review_result;
create policy ml_review_result_admin_select on public.ml_review_result
  for select to authenticated using (private.is_admin());

drop policy if exists ml_model_registry_admin_select on public.ml_model_registry;
create policy ml_model_registry_admin_select on public.ml_model_registry
  for select to authenticated using (private.is_admin());

revoke all on function
  private.ml_boxes_valid(jsonb, integer, integer),
  private.ml_image_eligible(uuid),
  private.ml_review_queue_before_insert(),
  private.ml_review_result_before_insert(),
  private.ml_review_result_after_insert(),
  private.ml_append_only_guard(),
  public.service_ml_audit(uuid, text, text, uuid, jsonb)
from public, anon, authenticated;
-- CHECK validator runs with the writer's privileges (service_role).
grant execute on function private.ml_boxes_valid(jsonb, integer, integer) to service_role;
grant execute on function public.service_ml_audit(uuid, text, text, uuid, jsonb) to service_role;
