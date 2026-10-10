#!/usr/bin/env python3
"""End-to-end right-to-delete check on the LOCAL stack (IMPLEMENTATION_PLAN item 1.5).

LOCAL ONLY (same guards as rls_http_check.py: localhost URL + local demo keys;
SQL inspection only through ``docker exec`` into the local DB container).

Flow, all through the real services:
  1. GoTrue: create account D (+ control account O) and sign D in (password grant)
     -> real session, refresh token, identity, GoTrue audit entries.
  2. D syncs two children (consent a/b/c, screening, brushing, referral) through
     PostgREST, uploads a dataset photo through the Storage API (flag ON for the
     test only) and registers it.
  3. D calls rpc/request_deletion('account') -> sync for D stops.
  4. Executor: the Edge Function ``process-deletion`` when it is being served
     locally (``--via edge``), otherwise (``--via rpc``) exactly the calls the
     function makes: service_pending_deletions -> Storage API remove ->
     service_execute_deletion.
  5. Proof: scans EVERY table of EVERY non-system schema (as the local superuser)
     for D's account id, both child ids, D's e-mail and the photo name; checks the
     blob is gone from the Storage container's disk; checks auth.sessions /
     refresh_tokens / identities / audit_log_entries; checks the proof row and the
     hashed audit_log entry; checks O is untouched. Container logs are reported
     (informational) because they are outside the database.

Run (from backend/, stack up):
    eval "$(npx supabase status -o env)"; export API_URL ANON_KEY SERVICE_ROLE_KEY
    python supabase/scripts/deletion_e2e_check.py [--via auto|edge|rpc] [--cron-secret ...]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import uuid

from local_stack import (CheckFailed, Checker, Stack, admin_create_user, admin_delete_user, ev, http, psql, rest,
                         rpc, sign_in, tiny_jpeg)

SCAN_SQL = r"""
do $scan$
declare
  t record;
  hit boolean;
  frag text;
begin
  create temp table _hits (frag text, tbl text);
  foreach frag in array string_to_array('__FRAGS__', ',') loop
    for t in
      select n.nspname, c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
      where c.relkind in ('r', 'p', 'm')
        and n.nspname not in ('pg_catalog', 'information_schema', 'pg_toast')
        and n.nspname not like 'pg_temp%' and n.nspname not like 'pg_toast_temp%'
    loop
      execute format('select exists (select 1 from %I.%I x where x::text ilike %L)',
                     t.nspname, t.relname, '%' || frag || '%') into hit;
      if hit then
        insert into _hits values (frag, t.nspname || '.' || t.relname);
      end if;
    end loop;
  end loop;
end
$scan$;
"""


def scan(stack: Stack, frags: dict[str, str]) -> dict[str, list[str]]:
    """Returns {label: [schema.table, ...]} for every fragment found anywhere."""
    for v in frags.values():
        if not all(ch.isalnum() or ch in "-@._" for ch in v):
            raise CheckFailed("unsafe scan fragment")
    values = ",".join(frags.values())
    sql = SCAN_SQL.replace("__FRAGS__", values) + "select frag || '|' || tbl from _hits order by 1;\n"
    out = psql(stack, sql, user="supabase_admin")
    back = {v: k for k, v in frags.items()}
    res: dict[str, list[str]] = {k: [] for k in frags}
    for line in out.splitlines():
        if "|" in line:
            f, tbl = line.split("|", 1)
            res[back[f]].append(tbl)
    return res


def edge_available(stack: Stack, secret: str) -> bool:
    r = http(stack, "OPTIONS", "/functions/v1/process-deletion")
    return r.status not in (404, 502, 503) and bool(secret)


def run_rpc_path(stack: Stack) -> dict:
    """Mirror of supabase/functions/process-deletion/index.ts using service_role."""
    r = rpc(stack, "service_pending_deletions", None, {"p_limit": 20}, service=True)
    if not r.ok:
        raise CheckFailed(f"service_pending_deletions HTTP {r.status}")
    completed = objects = 0
    for item in r.body:
        paths = item.get("storage_paths") or []
        removed = 0
        if paths:
            d = http(stack, "DELETE", "/storage/v1/object/dataset_photos", token=stack.service_key,
                     apikey=stack.service_key, body={"prefixes": paths})
            if not d.ok:
                raise CheckFailed(f"storage remove HTTP {d.status}")
            removed = len(d.body or [])
        objects += removed
        x = rpc(stack, "service_execute_deletion", None,
                {"p_request_id": item["request_id"], "p_storage_objects_deleted": removed}, service=True)
        if not x.ok:
            raise CheckFailed(f"service_execute_deletion HTTP {x.status} {x.body}")
        completed += (x.body or {}).get("status") == "completed"
    return {"requests": len(r.body), "completed": completed, "objects_removed": objects}


def container_log_hits(needles: list[str]) -> dict[str, int]:
    docker = shutil.which("docker")
    out: dict[str, int] = {}
    if not docker:
        return out
    for name in ("auth", "kong", "rest", "storage", "edge_runtime", "db"):
        cont = f"supabase_{name}_dentisai"
        r = subprocess.run([docker, "logs", cont], capture_output=True, text=True, encoding="utf-8", errors="replace")
        text = r.stdout + r.stderr
        out[name] = sum(text.count(n) for n in needles)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url")
    ap.add_argument("--via", choices=("auto", "edge", "rpc"), default="auto")
    ap.add_argument("--cron-secret", default=os.environ.get("LOCAL_CRON_SECRET", ""),
                    help="CRON_SECRET the locally served function was started with (local value only)")
    args = ap.parse_args()

    stack = Stack.from_env(args.url)
    c = Checker()
    run = uuid.uuid4().hex[:8]
    D = admin_create_user(stack, "deletee", run)
    O = admin_create_user(stack, "control", run)
    try:
        c.check(sign_in(stack, D) == "password", "D signs in through GoTrue (password grant)")
        sign_in(stack, O)
        # refresh once so a rotated refresh token exists as well
        x1 = str(uuid.uuid4())
        x2 = str(uuid.uuid4())
        o_child = str(uuid.uuid4())
        rest(stack, "PATCH", "feature_flag?key=eq.dataset_contribution", None,
             body={"enabled": True, "approval_ref": "LOCAL-TEST-ONLY"}, service=True)
        batch = []
        for child in (x1, x2):
            for t in ("a", "b", "c"):
                batch.append(ev("consent_changed", {"child_id": child, "consent_type": t, "action": "grant",
                                                    "text_version": "local-v1"}))
            batch.append(ev("child_upserted", {"child_id": child, "birth_year": 2016, "birth_month": 2,
                                               "allergies": ["mint"]}))
        sid = str(uuid.uuid4())
        batch += [
            ev("screening_recorded", {"screening_id": sid, "child_id": x1, "screened_at": "2026-10-01T08:00:00Z",
                                      "model_version": "0.1.0", "rules_version": "0.1.0", "level": "orange"}),
            ev("brushing_recorded", {"brushing_id": str(uuid.uuid4()), "child_id": x1,
                                     "started_at": "2026-10-02T07:00:00Z", "duration_s": 120}),
            ev("referral_created", {"referral_id": str(uuid.uuid4()), "child_id": x1, "screening_id": sid,
                                    "level": "orange"}),
        ]
        r = rpc(stack, "sync_events", D.token, {"batch": batch})
        c.check(r.ok and all(e["status"] == "applied" for e in r.body), "D: children + data synced via REST",
                f"HTTP {r.status}")
        r = rpc(stack, "sync_events", O.token, {"batch": [
            ev("consent_changed", {"child_id": o_child, "consent_type": "a", "action": "grant", "text_version": "v1"}),
            ev("consent_changed", {"child_id": o_child, "consent_type": "b", "action": "grant", "text_version": "v1"})]})
        c.check(r.ok, "O (control): child synced")

        upload_id = str(uuid.uuid4())
        photo = f"{D.id}/{x1}/{upload_id}.jpg"
        r = http(stack, "POST", f"/storage/v1/object/dataset_photos/{photo}", token=D.token, raw=tiny_jpeg(8192),
                 headers={"Content-Type": "image/jpeg"})
        c.check(r.ok, "D: dataset photo uploaded through the Storage API", f"HTTP {r.status} {r.body}")
        r = rpc(stack, "sync_events", D.token, {"batch": [ev("image_upload_registered", {
            "upload_id": upload_id, "child_id": x1, "object_path": photo})]})
        c.check(r.ok and r.body[0]["status"] == "applied", "D: photo registered (image_upload)", str(r.body))
        blob_before = psql(stack, f"select count(*) from storage.objects where name = '{photo}';")
        c.check(blob_before == "1", "fixture: storage.objects row exists")

        # 3. request
        r = rpc(stack, "request_deletion", D.token, {"p_scope": "account"})
        req = r.body if r.ok else None
        c.check(r.ok and isinstance(req, str), "D: rpc/request_deletion('account') accepted", f"HTTP {r.status}")
        r = rpc(stack, "sync_events", D.token, {"batch": [ev("brushing_recorded", {
            "brushing_id": str(uuid.uuid4()), "child_id": x2, "started_at": "2026-10-03T07:00:00Z", "duration_s": 90})]})
        c.check(r.ok and r.body[0].get("code") == "deletion_pending", "sync stops immediately after the request")

        # 4. execute
        via = args.via
        if via == "auto":
            via = "edge" if edge_available(stack, args.cron_secret) else "rpc"
        if via == "edge":
            r = http(stack, "POST", "/functions/v1/process-deletion", token=args.cron_secret, headers={})
            c.check(r.status == 200 and r.body.get("completed", 0) >= 1,
                    "Edge Function process-deletion completed the request", f"HTTP {r.status} {r.body}")
            bad = http(stack, "POST", "/functions/v1/process-deletion", token="wrong-secret-" + "x" * 40, headers={})
            c.check(bad.status == 403, "Edge Function rejects a wrong CRON_SECRET", f"HTTP {bad.status}")
        else:
            summary = run_rpc_path(stack)
            c.check(summary["completed"] >= 1 and summary["objects_removed"] == 1,
                    "service RPC path (= process-deletion logic) completed the request", str(summary))
        print(f"     executor: {via}")

        # 5. proof + no trace
        row = psql(stack, "select status || '|' || coalesce(executed_by,'') || '|' || (subject_id is null) || '|'"
                          " || (requested_by is null) || '|' || coalesce(storage_objects_deleted, -1) || '|'"
                          " || coalesce(rows_deleted->>'auth_user','') || '|' || coalesce(rows_deleted->>'child_profile','')"
                          " || '|' || (proof_sha256 ~ '^[0-9a-f]{64}$') || '|' || subject_hash"
                          f" from public.deletion_request where id = '{req}';")
        parts = row.split("|")
        c.check(len(parts) == 9 and parts[0] == "completed" and parts[2] == "true" and parts[3] == "true"
                and parts[4] == "1" and parts[5] == "1" and parts[6] == "2" and parts[7] == "true",
                "proof row: completed, subject/requester cleared, 1 object, auth_user=1, 2 children, sha256",
                row.split("|")[:8].__str__())
        subject_hash = parts[8] if len(parts) == 9 else ""
        n_audit = psql(stack, f"select count(*) from public.audit_log where action = 'deletion_completed'"
                              f" and target_hash = '{subject_hash}';")
        c.check(n_audit == "1", "audit_log: one hashed deletion_completed entry linked to the proof")
        hits = scan(stack, {"account_id": D.id, "child_1": x1, "child_2": x2, "email": D.email.lower(),
                            "photo_upload_id": upload_id})
        for label, tables in hits.items():
            c.check(not tables, f"no trace of {label} in ANY table of ANY schema", ", ".join(tables))
        pii = psql(stack, "select count(*) from public.deletion_request d, public.audit_log a"
                          f" where d.id = '{req}' and a.target_hash = d.subject_hash"
                          f" and (d::text || a::text) ~* '@|{D.id}';")
        c.check(pii == "0", "proof + audit entry contain no id or e-mail")
        for t in ("sessions", "refresh_tokens", "identities", "one_time_tokens", "flow_state", "mfa_factors"):
            col = "user_id"
            n = psql(stack, f"select count(*) from auth.{t} where {col}::text = '{D.id}';", user="supabase_admin")
            c.check(n == "0", f"auth.{t}: nothing left for D")
        n = psql(stack, "select count(*) from auth.audit_log_entries where payload::text ilike "
                        f"'%{D.id}%' or payload::text ilike '%{D.email.lower()}%';", user="supabase_admin")
        c.check(n == "0", "auth.audit_log_entries: no entry mentions D's id or e-mail", f"left={n}")
        r = http(stack, "GET", "/rest/v1/child_profile?select=id", token=D.token)
        c.check(r.status in (200, 401, 403) and (r.status != 200 or r.body == []),
                "D's still-unexpired JWT reads nothing afterwards", f"HTTP {r.status}")
        r = http(stack, "POST", "/auth/v1/token?grant_type=password",
                 body={"email": D.email, "password": D.password,
                       "gotrue_meta_security": {"captcha_token": "XXXX.DUMMY.TOKEN.XXXX"}})
        c.check(r.status == 400, "D can no longer sign in", f"HTTP {r.status}")
        docker = shutil.which("docker")
        if docker:
            f = subprocess.run([docker, "exec", "supabase_storage_dentisai", "sh", "-c",
                                f"find / -xdev -path '*{upload_id}*' 2>/dev/null | wc -l"],
                               capture_output=True, text=True)
            c.check(f.stdout.strip() == "0", "photo blob removed from the Storage container's disk",
                    f"found={f.stdout.strip()}")
        n = psql(stack, f"select count(*) from public.child_profile where id = '{o_child}';")
        c.check(n == "1", "control account O and its child untouched")

        logs = container_log_hits([D.email, D.id])
        print("     info: occurrences of D's e-mail/id in container logs (outside the DB): "
              + ", ".join(f"{k}={v}" for k, v in logs.items()))
    except CheckFailed as e:
        c.check(False, "unexpected failure", str(e))
    finally:
        rest(stack, "PATCH", "feature_flag?key=eq.dataset_contribution", None,
             body={"enabled": False, "approval_ref": None}, service=True)
        admin_delete_user(stack, O.id)
        admin_delete_user(stack, D.id)   # no-op when the deletion worked
    return c.summary()


if __name__ == "__main__":
    sys.exit(main())
