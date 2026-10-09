-- =============================================================================
-- DentisAI · 0100 foundation: preconditions, extensions, schemas, privileges, enums
-- =============================================================================
-- Every table in this project uses ENABLE + FORCE ROW LEVEL SECURITY. SECURITY
-- DEFINER helpers (schema `private`) are owned by the migration role and must be
-- able to read across tables, so that role needs BYPASSRLS (the Supabase
-- `postgres` role has it [PERLU VERIFIKASI on hosted project: run
-- `select rolbypassrls from pg_roles where rolname = 'postgres'`]). Fail loudly
-- instead of silently producing helpers that always return "deny".
do $$
declare
  r text;
begin
  if not exists (
    select 1 from pg_catalog.pg_roles
    where rolname = current_user and (rolsuper or rolbypassrls)
  ) then
    raise exception 'DentisAI: migration role must have BYPASSRLS (FORCE RLS + SECURITY DEFINER helpers)';
  end if;
  foreach r in array array['anon', 'authenticated', 'service_role'] loop
    if not exists (select 1 from pg_catalog.pg_roles where rolname = r) then
      raise exception 'DentisAI: required Supabase role is missing: %', r;
    end if;
  end loop;
end
$$;

create schema if not exists extensions;
create extension if not exists pgcrypto with schema extensions;

-- `private` is NOT exposed through PostgREST (config.toml api.schemas = public).
-- authenticated needs USAGE because RLS policy expressions call private helpers
-- with the caller's privileges.
create schema if not exists private;
revoke all on schema private from public;
revoke all on schema private from anon;
grant usage on schema private to authenticated, service_role;
comment on schema private is 'DentisAI internal helpers, secrets and jobs. Never exposed via the Data API.';

-- -----------------------------------------------------------------------------
-- Default privileges: nothing created by this role is auto-granted to clients.
-- Supabase ships per-schema defaults that grant ALL to anon/authenticated on
-- public; revoke them, and revoke the global EXECUTE-to-PUBLIC default for
-- functions. Every grant in later migrations is explicit.
-- -----------------------------------------------------------------------------
alter default privileges in schema public revoke all on tables from anon, authenticated, public;
alter default privileges in schema public revoke all on sequences from anon, authenticated, public;
alter default privileges in schema public revoke all on functions from anon, authenticated, public;
alter default privileges revoke execute on functions from public;

revoke all on all tables in schema public from anon, authenticated, public;
revoke all on all sequences in schema public from anon, authenticated, public;
revoke all on all functions in schema public from anon, authenticated, public;

-- -----------------------------------------------------------------------------
-- Enums (guarded so the file can be re-applied on a partially migrated DB)
-- -----------------------------------------------------------------------------
do $$ begin
  create type public.app_role as enum ('guardian', 'kader', 'facility_viewer', 'admin');
exception when duplicate_object then null; end $$;
comment on type public.app_role is 'guardian = orang tua; kader = kader/guru UKS (mode kelas); facility_viewer = puskesmas/Dinkes, aggregates only; admin = audited operations.';

do $$ begin
  -- a = simpan lokal, b = kirim data teknis terpseudonim (sync), c = unggah foto (dataset)
  create type public.consent_type as enum ('a', 'b', 'c');
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.consent_method as enum ('in_app', 'paper_via_kader');
exception when duplicate_object then null; end $$;

do $$ begin
  -- Mirrors the four UI levels (merah / oranye / kuning / hijau). Clinical meaning
  -- lives in triage/rules.yaml, not here.
  create type public.triage_level as enum ('red', 'orange', 'yellow', 'green');
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.image_upload_status as enum ('uploaded', 'withdrawn', 'deleted');
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.deletion_scope as enum ('child', 'account');
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.deletion_status as enum ('pending', 'processing', 'completed', 'failed');
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.brushing_slot as enum ('morning', 'evening', 'other');
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.sync_event_type as enum (
    'consent_changed',
    'child_upserted',
    'screening_recorded',
    'brushing_recorded',
    'referral_created',
    'image_upload_registered'
  );
exception when duplicate_object then null; end $$;
