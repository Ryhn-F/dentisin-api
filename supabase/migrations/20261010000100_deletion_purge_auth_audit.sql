-- Right-to-delete fix (FR-102), found by supabase/scripts/deletion_e2e_check.py on the
-- real local stack (CLI 2.120, GoTrue v2.197): deleting the auth.users row leaves
-- GoTrue's own audit trail behind. auth.audit_log_entries has no foreign key to
-- auth.users and stores the account id and e-mail in its JSON payload
-- (actor_id, actor_username, traits.user_id, traits.user_email), so after an
-- "account" deletion the id and e-mail were still in the database.
--
-- Additive: redefines private.execute_deletion (same signature, same behaviour)
-- and, for scope = 'account', also purges every GoTrue audit entry that mentions
-- the account id or its exact e-mail. The count goes into the proof
-- (rows_deleted.auth_audit_log_entries). Matching is exact, never LIKE:
--   * the id is a uuid -> substring of the payload text is unambiguous;
--   * the e-mail is matched as a complete JSON string ("<email>") so
--     bob@x.test never matches alice.bob@x.test.
-- Hosted note [PERLU VERIFIKASI]: requires the function owner (postgres) to keep
-- DELETE on auth.audit_log_entries on the hosted project (true on the local
-- image). GoTrue logs shipped to the platform log store (outside Postgres) are not
-- covered by this; they expire with the plan's log retention.

create or replace function private.execute_deletion(
  p_request_id uuid,
  p_executed_by text,
  p_storage_objects_deleted integer default 0
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  r public.deletion_request%rowtype;
  v_children uuid[];
  v_counts jsonb;
  v_hash text;
  v_now timestamptz := now();
  v_proof text;
  v_extra jsonb := '{}'::jsonb;
  v_n bigint;
  v_email text;
begin
  select * into r from public.deletion_request d where d.id = p_request_id for update;
  if not found or r.status not in ('pending', 'processing') then
    return jsonb_build_object('status', 'skipped');
  end if;

  update public.deletion_request d
     set status = 'processing', started_at = coalesce(d.started_at, v_now), attempts = d.attempts + 1
   where d.id = r.id;

  if exists (select 1 from private.deletion_storage_paths(r.scope, r.subject_id)) then
    update public.deletion_request d
       set failure_code = 'awaiting_storage_purge',
           storage_objects_deleted = coalesce(d.storage_objects_deleted, 0) + greatest(coalesce(p_storage_objects_deleted, 0), 0)
     where d.id = r.id;
    return jsonb_build_object('status', 'awaiting_storage_purge');
  end if;

  select coalesce(array_agg(x), '{}'::uuid[]) into v_children
  from private.deletion_children(r.scope, r.subject_id) x;
  v_counts := private.count_child_rows(v_children);

  delete from public.child_profile c where c.id = any (v_children);   -- cascades

  if r.scope = 'account' then
    select count(*) into v_n from public.class_roster where kader_id = r.subject_id;
    v_extra := v_extra || jsonb_build_object('class_roster', v_n);
    delete from public.class_roster where kader_id = r.subject_id;

    select count(*) into v_n from public.sync_event where submitted_by = r.subject_id;
    v_extra := v_extra || jsonb_build_object('sync_event_account', v_n);
    delete from public.sync_event where submitted_by = r.subject_id;

    select count(*) into v_n from public.guardian where id = r.subject_id;
    v_extra := v_extra || jsonb_build_object('guardian', v_n);
    select count(*) into v_n from public.user_role where user_id = r.subject_id;
    v_extra := v_extra || jsonb_build_object('user_role', v_n);
    delete from public.guardian where id = r.subject_id;
    delete from public.user_role where user_id = r.subject_id;

    -- Other (older, completed) requests by this account lose the link.
    update public.deletion_request d set requested_by = null
     where d.requested_by = r.subject_id and d.id <> r.id;

    -- The auth account itself (cascades to auth.identities/sessions/etc.).
    select lower(u.email) into v_email from auth.users u where u.id = r.subject_id;
    select count(*) into v_n from auth.users where id = r.subject_id;
    v_extra := v_extra || jsonb_build_object('auth_user', v_n);
    delete from auth.users where id = r.subject_id;

    -- GoTrue's audit trail (no FK to auth.users): id / exact e-mail in the payload.
    delete from auth.audit_log_entries a
     where strpos(a.payload::text, r.subject_id::text) > 0
        or (v_email is not null and strpos(lower(a.payload::text), '"' || v_email || '"') > 0);
    get diagnostics v_n = row_count;
    v_extra := v_extra || jsonb_build_object('auth_audit_log_entries', v_n);
  end if;

  v_counts := v_counts || v_extra;
  v_hash := private.hash_ref(r.subject_id);
  v_proof := r.id::text || '|' || r.scope::text || '|' || v_hash || '|' || v_counts::text || '|'
             || (coalesce(r.storage_objects_deleted, 0) + greatest(coalesce(p_storage_objects_deleted, 0), 0))::text
             || '|' || to_char(v_now at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"');

  update public.deletion_request d
     set status = 'completed',
         subject_id = null,
         requested_by = case when r.scope = 'account' then null else d.requested_by end,
         completed_at = v_now,
         executed_by = p_executed_by,
         subject_hash = v_hash,
         rows_deleted = v_counts,
         storage_objects_deleted = coalesce(d.storage_objects_deleted, 0) + greatest(coalesce(p_storage_objects_deleted, 0), 0),
         proof_sha256 = encode(extensions.digest(convert_to(v_proof, 'UTF8'), 'sha256'), 'hex'),
         failure_code = null
   where d.id = r.id;

  insert into public.audit_log (actor_role, actor_hash, action, target_table, target_hash, details)
  values (replace(p_executed_by, 'edge:', 'edge_'), null, 'deletion_completed', 'deletion_request', v_hash,
          jsonb_build_object('scope', r.scope::text, 'children', cardinality(v_children)));

  return jsonb_build_object('status', 'completed', 'rows_deleted', v_counts);
end
$$;

revoke all on function private.execute_deletion(uuid, text, integer)
from public, anon, authenticated, service_role;
