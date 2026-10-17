-- =============================================================================
-- DentisAI · 20261017000100 account model v2 (ADR 017) — part 1/6: enum types
-- =============================================================================
-- Kept in its OWN migration on purpose: a value added with ALTER TYPE ... ADD
-- VALUE cannot be used in the same transaction, and later migrations (policies,
-- functions, CHECKs) reference 'child', 'd', 'screening_requested' and
-- 'brushing_proof_registered'. Nothing in this file uses the new values.
-- Re-runnable (guarded CREATE TYPE, ADD VALUE IF NOT EXISTS).

do $$ begin
  -- guardian = orang tua (default); teen = remaja, one self profile;
  -- child = account created by a guardian through the Edge Function child-accounts.
  create type public.account_type as enum ('guardian', 'teen', 'child');
exception when duplicate_object then null; end $$;
comment on type public.account_type is
  'ADR 017. guardian/teen come from signUp (raw_user_meta_data, only these two values are accepted); child only from server-controlled raw_app_meta_data.';

do $$ begin
  create type public.request_status as enum ('pending', 'seen', 'done', 'cancelled', 'expired');
exception when duplicate_object then null; end $$;
comment on type public.request_status is
  'screening_request lifecycle: pending -> seen -> done | cancelled | expired (ADR 017 §2.4).';

do $$ begin
  create type public.proof_status as enum ('uploaded', 'viewed', 'deleted', 'expired');
exception when duplicate_object then null; end $$;
comment on type public.proof_status is
  'brushing_proof lifecycle (ADR 017 §2.5). deleted = by the guardian or consent (d) withdrawn; expired = retention passed.';

alter type public.app_role add value if not exists 'child';
-- d = brushing proof video shared with the guardian (ADR 017 §2.5)
alter type public.consent_type add value if not exists 'd';
alter type public.sync_event_type add value if not exists 'screening_requested';
alter type public.sync_event_type add value if not exists 'brushing_proof_registered';
