-- =============================================================================
-- LOCAL HARNESS ONLY — tiny pgTAP-compatible subset, used when the real pgTAP
-- extension is not installed (plain PostgreSQL 17 on Windows). Implements only
-- what backend/supabase/tests/*.test.sql use:
--   plan(int), ok(bool,text), is(anyelement,anyelement,text),
--   isnt(anyelement,anyelement,text), throws_ok(text,text,text,text),
--   lives_ok(text,text), is_empty(text,text), results_eq(text,text,text),
--   finish()
-- Output lines follow TAP ("ok N - desc" / "not ok N - desc"). State is kept in
-- custom GUCs so it survives SET ROLE inside a test transaction.
-- The canonical runner remains `supabase test db` with real pgTAP.
-- =============================================================================
create or replace function extensions._tap_next(p_ok boolean, p_desc text, p_diag text default null)
returns text language plpgsql as $$
declare
  n int := coalesce(nullif(current_setting('tap.n', true), ''), '0')::int + 1;
  f int := coalesce(nullif(current_setting('tap.failed', true), ''), '0')::int;
begin
  perform set_config('tap.n', n::text, false);
  if not coalesce(p_ok, false) then
    perform set_config('tap.failed', (f + 1)::text, false);
    return 'not ok ' || n || ' - ' || coalesce(p_desc, '')
           || coalesce(E'\n# ' || p_diag, '');
  end if;
  return 'ok ' || n || ' - ' || coalesce(p_desc, '');
end $$;

create or replace function extensions.plan(p int) returns text language plpgsql as $$
begin
  perform set_config('tap.n', '0', false);
  perform set_config('tap.failed', '0', false);
  perform set_config('tap.plan', p::text, false);
  return '1..' || p;
end $$;

create or replace function extensions.ok(p boolean, p_desc text default null) returns text
language sql as $$ select extensions._tap_next(p, p_desc) $$;

create or replace function extensions.is(a anyelement, b anyelement, p_desc text default null) returns text
language sql as $$
  select extensions._tap_next(a is not distinct from b, p_desc,
    case when a is distinct from b then 'have: ' || coalesce(a::text, 'NULL') || ' want: ' || coalesce(b::text, 'NULL') end)
$$;

create or replace function extensions.isnt(a anyelement, b anyelement, p_desc text default null) returns text
language sql as $$ select extensions._tap_next(a is distinct from b, p_desc) $$;

create or replace function extensions.throws_ok(p_sql text, p_errcode text, p_errmsg text, p_desc text)
returns text language plpgsql as $$
begin
  begin
    execute p_sql;
  exception when others then
    if (p_errcode is null or sqlstate = p_errcode) and (p_errmsg is null or sqlerrm = p_errmsg) then
      return extensions._tap_next(true, p_desc);
    end if;
    return extensions._tap_next(false, p_desc, 'caught ' || sqlstate || ': ' || sqlerrm);
  end;
  return extensions._tap_next(false, p_desc, 'no exception thrown');
end $$;

create or replace function extensions.lives_ok(p_sql text, p_desc text default null)
returns text language plpgsql as $$
begin
  begin
    execute p_sql;
  exception when others then
    return extensions._tap_next(false, p_desc, 'died ' || sqlstate || ': ' || sqlerrm);
  end;
  return extensions._tap_next(true, p_desc);
end $$;

create or replace function extensions.is_empty(p_sql text, p_desc text default null)
returns text language plpgsql as $$
declare
  v text[];
begin
  execute format('select array_agg(t::text) from (%s) t', p_sql) into v;
  return extensions._tap_next(v is null, p_desc,
    case when v is not null then 'unexpected rows: ' || left(array_to_string(v, ' | '), 500) end);
end $$;

create or replace function extensions.results_eq(p_have text, p_want text, p_desc text default null)
returns text language plpgsql as $$
declare
  h text[];
  w text[];
begin
  execute format('select array_agg(t::text) from (%s) t', p_have) into h;
  execute format('select array_agg(t::text) from (%s) t', p_want) into w;
  return extensions._tap_next(h is not distinct from w, p_desc,
    case when h is distinct from w then 'have: ' || coalesce(left(h::text, 300), 'NULL')
                                   || ' want: ' || coalesce(left(w::text, 300), 'NULL') end);
end $$;

create or replace function extensions.finish() returns setof text language plpgsql as $$
declare
  n int := coalesce(nullif(current_setting('tap.n', true), ''), '0')::int;
  f int := coalesce(nullif(current_setting('tap.failed', true), ''), '0')::int;
  p int := coalesce(nullif(current_setting('tap.plan', true), ''), '-1')::int;
begin
  if p <> n then
    return next '# Looks like you planned ' || p || ' tests but ran ' || n;
  end if;
  if f > 0 then
    return next '# Looks like you failed ' || f || ' test of ' || n;
  end if;
  return;
end $$;

grant execute on all functions in schema extensions to public;
