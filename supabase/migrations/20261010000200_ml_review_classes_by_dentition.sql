-- =============================================================================
-- DentisAI · 20261010000200 ML review classes by dentition (ADR 014)
-- =============================================================================
-- Additive. Supersedes the class vocabulary of 20261009000100 (never edited:
-- already applied):
--   * ml_review_result boxes: cls in ('caries_permanent' [label D],
--     'caries_primary' [label d]) replaces caries_cavitated / caries_noncavitated.
--     The dataset has no cavitated / non-cavitated distinction (ADR 014 #1).
--   * Boxes are annotation / UI-overlay geometry only. Severity is NEVER derived
--     from box size (ADR 014 #2), so screening findings no longer accept the
--     derived `rel_area` key (private.findings_valid redefined without it).
--
-- Safety: the old CHECK constraint is dropped and re-added NOT VALID (new and
-- updated rows are checked immediately), then VALIDATEd only when no existing
-- row violates it; otherwise the migration aborts with a clear message and
-- changes nothing (single transaction). Existing legacy rows must be converted
-- or deleted by a human first (a class cannot be mapped automatically).
-- RLS stays ENABLED + FORCED, all functions keep search_path = '' and their
-- grants. Re-runnable (the local harness applies every migration twice).

-- Boxes validator: same rules as before, new class vocabulary.
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
    if jsonb_typeof(item -> 'cls') is distinct from 'string'
       or (item ->> 'cls') not in ('caries_permanent', 'caries_primary') then
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

revoke all on function private.ml_boxes_valid(jsonb, integer, integer) from public, anon, authenticated;
grant execute on function private.ml_boxes_valid(jsonb, integer, integer) to service_role;

-- Swap the constraint: NOT VALID first, VALIDATE only if clean.
alter table public.ml_review_result drop constraint if exists ml_review_result_boxes_valid;
alter table public.ml_review_result
  add constraint ml_review_result_boxes_valid
  check (private.ml_boxes_valid(boxes, image_width, image_height)) not valid;

do $$
declare
  v_bad bigint;
begin
  select count(*) into v_bad
  from public.ml_review_result r
  where not private.ml_boxes_valid(r.boxes, r.image_width, r.image_height);
  if v_bad > 0 then
    raise exception
      'ml_review_result: % row(s) violate the ADR 014 class vocabulary (legacy caries_cavitated/caries_noncavitated?); convert or delete them, then re-run this migration',
      v_bad
      using errcode = '23514';
  end if;
  alter table public.ml_review_result validate constraint ml_review_result_boxes_valid;
end
$$;

comment on column public.ml_review_result.boxes is
  'dentis-canonical-v1 boxes: [{cls, x1, y1, x2, y2}] absolute pixels of the original image; cls in (caries_permanent, caries_primary) per ADR 014. Annotation/overlay only: never derive severity from box size.';

-- Screening findings: ADR 014 removes every box-size-derived field. `rel_area`
-- is no longer accepted; key set is class, confidence, box (overlay geometry).
-- Class stays a code (^[a-z][a-z0-9_]{1,39}$) so a new model version needs no migration.
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
      if k not in ('class', 'confidence', 'box') then
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

comment on function private.findings_valid(jsonb) is
  'Detector findings: <= 50 objects with only class (code), confidence (0..1), box ([x,y,w,h] in 0..1, overlay only). No box-size-derived field (ADR 014).';

-- Keep the grants of the original definition (create or replace preserves them;
-- re-assert so a fresh reading of this file is self-contained).
revoke all on function private.findings_valid(jsonb) from public, anon;
grant execute on function private.findings_valid(jsonb) to authenticated, service_role;

alter table public.ml_review_result enable row level security;
alter table public.ml_review_result force row level security;
