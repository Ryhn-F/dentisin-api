-- Meta / guard-rail tests: these fail when a NEW migration forgets RLS, leaks a
-- grant to anon, adds identity columns, or ships an unsafe SECURITY DEFINER.
begin;
\ir _helpers.psql
select plan(16);

select is_empty($$
  select n.nspname || '.' || c.relname
  from pg_class c join pg_namespace n on n.oid = c.relnamespace
  where n.nspname in ('public', 'private') and c.relkind in ('r', 'p')
    and not (c.relrowsecurity and c.relforcerowsecurity)
$$, 'every table in public/private has RLS ENABLED and FORCED');

select is_empty($$
  select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
  where n.nspname = 'public' and c.relkind in ('r', 'p', 'v', 'm', 'f')
    and (has_table_privilege('anon', c.oid, 'SELECT') or has_table_privilege('anon', c.oid, 'INSERT')
         or has_table_privilege('anon', c.oid, 'UPDATE') or has_table_privilege('anon', c.oid, 'DELETE'))
$$, 'anon has no privilege on any public table/view');

select is_empty($$
  select p.proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace
  where n.nspname = 'public' and has_function_privilege('anon', p.oid, 'EXECUTE')
    and p.proname <> 'keepalive'
$$, 'anon can execute only public.keepalive()');

select ok(not has_schema_privilege('anon', 'private', 'USAGE'), 'anon has no USAGE on schema private');

select is_empty($$
  select n.nspname || '.' || p.proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace
  where n.nspname in ('public', 'private') and p.prosecdef
    and not coalesce(p.proconfig @> array['search_path=""'], false)
$$, 'every SECURITY DEFINER function pins search_path = ''''');

select is_empty($$
  select table_name || '.' || column_name from information_schema.columns
  where table_schema = 'public'
    and table_name in ('child_profile', 'guardian', 'screening', 'consent', 'brushing_proof')
    and column_name ~* '(name|nama|nik|nisn|dob|birth_date|birthday|tanggal_lahir|address|alamat|phone|telp|email|photo|image|foto|day)'
$$, 'no identity / exact-DOB / photo columns on child-facing tables');

select is_empty($$
  select table_name || '.' || column_name from information_schema.columns
  where table_schema = 'public' and data_type = 'bytea'
$$, 'no binary (image) columns anywhere in public');

select is_empty($$
  select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
  where n.nspname = 'public' and c.relkind in ('v', 'm')
    and not coalesce(c.reloptions @> array['security_invoker=true'], false)
$$, 'public views (if any) are security_invoker');

select is((select enabled from public.feature_flag where key = 'dataset_contribution'), false,
          'dataset_contribution flag defaults to OFF');

select is_empty($$
  select t || ':' || p from unnest(array['user_role', 'audit_log', 'sync_event', 'feature_flag', 'app_setting']) t,
       unnest(array['INSERT', 'UPDATE', 'DELETE']) p
  where has_table_privilege('authenticated', 'public.' || t, p)
$$, 'authenticated cannot write user_role/audit_log/sync_event/feature_flag/app_setting');

select ok(not has_table_privilege('authenticated', 'private.secret', 'SELECT')
          and not has_table_privilege('service_role', 'private.secret', 'SELECT'),
          'nobody but the owner can read the audit pepper');

select results_eq(
  $$ select public, file_size_limit, allowed_mime_types from storage.buckets where id = 'dataset_photos' $$,
  $$ values (false, 512000::bigint, array['image/jpeg']::text[]) $$,
  'dataset_photos: private, 500 KiB, JPEG only');

select is((select public from storage.buckets where id = 'manifests'), true, 'manifests bucket is public-read');

select is_empty($$
  select policyname from pg_policies
  where schemaname = 'storage' and tablename = 'objects'
    and policyname like 'dentisai_dataset%' and cmd <> 'INSERT'
$$, 'dataset_photos has only an INSERT policy (no select/update/delete for users)');

select is_empty($$
  select policyname from pg_policies
  where schemaname in ('public', 'private') and 'anon' = any (roles)
$$, 'no policy targets anon');

select ok((select count(*) from pg_policies where schemaname = 'public') >= 30,
          'explicit policies exist for public tables');

select * from finish();
rollback;
