#!/usr/bin/env python3
"""RLS / Storage checks through the REAL local HTTP stack (Kong -> PostgREST,
GoTrue, Storage API) with real JWTs — backend/docs/IMPLEMENTATION_PLAN.md item 1.6.

LOCAL ONLY. Refuses any URL that is not localhost/127.0.0.1/[::1] and any key that
is not a local demo key (see local_stack.py). Stdlib only.

Run (from backend/, with the local stack up — see supabase/README.md):
    eval "$(npx supabase status -o env)"; export API_URL ANON_KEY SERVICE_ROLE_KEY
    python supabase/scripts/rls_http_check.py

What it does (re-runnable; every run uses fresh random accounts and ids):
  * creates 6 accounts through the GoTrue admin API (no e-mail is sent) and signs
    each one in through GoTrue (password grant + CAPTCHA test token) to obtain
    real access tokens: guardian A, guardian B, kader K, facility_viewer FV,
    admin AD (bootstrapped with service_role, like the README's first-admin
    step), plus an extra account for role-escalation probes;
  * asserts row isolation, role self-escalation, sync_events idempotency, Storage
    upload rules and the manifests bucket — all via HTTP;
  * cleans up: test objects removed through the Storage API, flag restored OFF,
    test accounts deleted through the GoTrue admin API (cascades their rows).
"""

from __future__ import annotations

import argparse
import sys
import uuid

from local_stack import (CheckFailed, Checker, Stack, User, admin_create_user, admin_delete_user, ev, http,
                         make_child_via_sync, psql, rest, rpc, sign_in, tiny_jpeg)


def storage_upload(stack: Stack, token: str | None, bucket: str, name: str, data: bytes,
                   content_type: str = "image/jpeg", service: bool = False, upsert: bool = False):
    hdr = {"Content-Type": content_type, "x-upsert": "true" if upsert else "false"}
    if service:
        return http(stack, "POST", f"/storage/v1/object/{bucket}/{name}", token=stack.service_key,
                    apikey=stack.service_key, raw=data, headers=hdr)
    return http(stack, "POST", f"/storage/v1/object/{bucket}/{name}", token=token, raw=data, headers=hdr)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", help="local API URL (default: $API_URL or http://127.0.0.1:54321)")
    ap.add_argument("--keep", action="store_true", help="do not delete the test accounts afterwards")
    args = ap.parse_args()

    stack = Stack.from_env(args.url)
    c = Checker()
    run = uuid.uuid4().hex[:8]
    users: dict[str, User] = {}
    uploaded: list[tuple[str, str]] = []
    try:
        for label in ("a", "b", "k", "fv", "ad"):
            users[label] = admin_create_user(stack, label, run)
        A, B, K, FV, AD = (users[x] for x in ("a", "b", "k", "fv", "ad"))

        # Bootstrap the first admin exactly like the README (owner/service side).
        r = rest(stack, "POST", "user_role", None, body={"user_id": AD.id, "role": "admin"},
                 prefer="return=minimal", service=True)
        c.check(r.ok, "bootstrap: service_role can insert the first admin role", f"HTTP {r.status}")
        rest(stack, "DELETE", f"user_role?user_id=eq.{AD.id}&role=eq.guardian", None, service=True)

        methods = {u.label: sign_in(stack, u) for u in users.values()}
        c.check(all(m == "password" for m in methods.values()),
                "GoTrue password grant (with CAPTCHA test token) issues real JWTs", str(set(methods.values())))

        # Admin grants kader / facility_viewer through the audited RPC (over REST).
        r = rpc(stack, "admin_grant_role", AD.token, {"p_user": K.id, "p_role": "kader", "p_facility_code": "TST-HTTP-1"})
        c.check(r.ok, "admin grants kader via REST RPC", f"HTTP {r.status} {r.body}")
        r = rpc(stack, "admin_grant_role", AD.token,
                {"p_user": FV.id, "p_role": "facility_viewer", "p_facility_code": "TST-HTTP-1"})
        c.check(r.ok, "admin grants facility_viewer via REST RPC", f"HTTP {r.status} {r.body}")

        # ------------------------------------------------------------------ anon
        r = rest(stack, "GET", "child_profile?select=id", None)
        c.check(r.status in (401, 403), "anon: GET child_profile denied", f"HTTP {r.status}")
        r = rest(stack, "GET", "feature_flag?select=key", None)
        c.check(r.status in (401, 403), "anon: GET feature_flag denied", f"HTTP {r.status}")
        r = rpc(stack, "sync_events", None, {"batch": [ev("brushing_recorded", {"child_id": str(uuid.uuid4())})]})
        c.check(r.status in (401, 403), "anon: rpc/sync_events denied", f"HTTP {r.status}")
        r = rpc(stack, "keepalive", None)
        c.check(r.ok and r.body == 1, "anon: rpc/keepalive returns 1", f"HTTP {r.status} {r.body}")
        r = rest(stack, "GET", "audit_log?select=id", None)
        c.check(r.status in (401, 403), "anon: GET audit_log denied", f"HTTP {r.status}")
        r = http(stack, "GET", "/rest/v1/", headers={})
        c.check(not (r.ok and isinstance(r.body, dict) and "child_profile" in str(r.body.get("paths", {}))),
                "anon: OpenAPI root does not describe child tables", f"HTTP {r.status}")

        # ------------------------------------------------------- A vs B isolation
        a_child = make_child_via_sync(stack, A)
        b_child = make_child_via_sync(stack, B)
        k_child = make_child_via_sync(stack, K)
        for who, child, lvl in ((A, a_child, "green"), (B, b_child, "orange"), (K, k_child, "yellow")):
            r = rpc(stack, "sync_events", who.token, {"batch": [ev("screening_recorded", {
                "screening_id": str(uuid.uuid4()), "child_id": child, "screened_at": "2026-10-01T08:00:00Z",
                "model_version": "0.1.0", "rules_version": "0.1.0", "level": lvl})]})
            c.check(r.ok and r.body[0]["status"] == "applied", f"{who.label.upper()}: screening synced via REST",
                    f"HTTP {r.status} {r.body}")

        r = rest(stack, "GET", "child_profile?select=id", A.token)
        c.check(r.ok and [x["id"] for x in r.body] == [a_child], "A: GET child_profile returns only own child",
                f"HTTP {r.status} n={len(r.body) if isinstance(r.body, list) else '?'}")
        r = rest(stack, "GET", f"child_profile?id=eq.{b_child}", A.token)
        c.check(r.ok and r.body == [], "A: filter on B's child id returns nothing")
        r = rest(stack, "GET", f"screening?child_id=eq.{b_child}", A.token)
        c.check(r.ok and r.body == [], "A: cannot read B's screenings")
        r = rest(stack, "GET", f"consent?child_id=eq.{b_child}", A.token)
        c.check(r.ok and r.body == [], "A: cannot read B's consent")
        r = rest(stack, "GET", "guardian?select=id", A.token)
        c.check(r.ok and [x["id"] for x in r.body] == [A.id], "A: guardian returns only own row")
        r = rest(stack, "GET", "sync_event?select=event_id,submitted_by", A.token)
        c.check(r.ok and r.body and all(x["submitted_by"] == A.id for x in r.body), "A: sync_event ledger only own rows")
        r = rest(stack, "PATCH", f"child_profile?id=eq.{b_child}", A.token, body={"birth_year": 2010},
                 prefer="return=representation")
        c.check(r.ok and r.body == [], "A: PATCH B's child affects 0 rows", f"HTTP {r.status} {r.body}")
        r = rest(stack, "GET", f"child_profile?id=eq.{b_child}&select=birth_year", None, service=True)
        c.check(r.ok and r.body and r.body[0]["birth_year"] == 2017, "... B's child unchanged (service check)")
        r = rest(stack, "DELETE", f"child_profile?id=eq.{a_child}", A.token)
        c.check(r.status in (401, 403), "A: DELETE child_profile not granted (use request_deletion)", f"HTTP {r.status}")
        r = rest(stack, "POST", "screening", A.token, body={
            "id": str(uuid.uuid4()), "child_id": b_child, "recorded_by": A.id, "screened_at": "2026-10-01T08:00:00Z",
            "model_version": "0.1.0", "rules_version": "0.1.0", "level": "green"})
        c.check(r.status in (401, 403), "A: direct POST screening for B's child denied by RLS", f"HTTP {r.status}")
        r = rest(stack, "POST", "child_profile", A.token, body={"id": str(uuid.uuid4()), "owner_id": B.id})
        c.check(r.status in (401, 403), "A: cannot create a child owned by B", f"HTTP {r.status}")
        r = rest(stack, "GET", "audit_log?select=id", A.token)
        c.check(r.ok and r.body == [], "A: audit_log returns nothing (admin only)")
        r = rest(stack, "GET", "app_setting?select=key", A.token)
        c.check(r.ok and r.body == [], "A: app_setting returns nothing (admin only)")
        r = rest(stack, "GET", "child_profile?select=id", B.token)
        c.check(r.ok and [x["id"] for x in r.body] == [b_child], "B: sees only own child")

        # ------------------------------------------------- role self-escalation
        r = rest(stack, "PATCH", f"user_role?user_id=eq.{A.id}", A.token, body={"role": "admin"})
        c.check(r.status in (401, 403), "A: PATCH own user_role -> admin blocked", f"HTTP {r.status}")
        r = rest(stack, "POST", "user_role", A.token, body={"user_id": A.id, "role": "admin"})
        c.check(r.status in (401, 403), "A: POST user_role (admin) blocked", f"HTTP {r.status}")
        r = rest(stack, "DELETE", f"user_role?user_id=eq.{A.id}", A.token)
        c.check(r.status in (401, 403), "A: DELETE user_role blocked", f"HTTP {r.status}")
        r = rpc(stack, "admin_grant_role", A.token, {"p_user": A.id, "p_role": "admin"})
        c.check(r.status in (401, 403), "A: rpc/admin_grant_role forbidden", f"HTTP {r.status}")
        r = rpc(stack, "admin_set_feature_flag", A.token,
                {"p_key": "dataset_contribution", "p_enabled": True, "p_approval_ref": "X"})
        c.check(r.status in (401, 403), "A: rpc/admin_set_feature_flag forbidden", f"HTTP {r.status}")
        r = rest(stack, "PATCH", "feature_flag?key=eq.dataset_contribution", A.token, body={"enabled": True})
        c.check(r.status in (401, 403), "A: PATCH feature_flag blocked", f"HTTP {r.status}")
        r = rpc(stack, "service_pending_deletions", A.token, {"p_limit": 5})
        c.check(r.status in (401, 403), "A: service_* RPC forbidden", f"HTTP {r.status}")
        # user_metadata is user-writable in GoTrue: it must not influence roles.
        r = http(stack, "PUT", "/auth/v1/user", token=A.token, body={"data": {"role": "admin", "app_role": "admin"}})
        c.check(r.ok, "A: can write own user_metadata (GoTrue allows it)", f"HTTP {r.status}")
        sign_in(stack, A)
        r = rest(stack, "GET", "audit_log?select=id&limit=1", A.token)
        c.check(r.ok and r.body == [], "... fresh JWT with role hints in user_metadata is still not admin")
        r = rest(stack, "GET", "user_role?select=role", A.token)
        c.check(r.ok and [x["role"] for x in r.body] == ["guardian"], "... A still has only 'guardian'")
        # A forged JWT (wrong signature) is rejected by PostgREST.
        forged = A.token.rsplit(".", 1)[0] + ".AAAA"
        r = rest(stack, "GET", "child_profile?select=id", forged)
        c.check(r.status == 401, "forged JWT signature rejected (401)", f"HTTP {r.status}")

        # ---------------------------------------------------- sync idempotency
        eid = str(uuid.uuid4())
        payload = {"brushing_id": str(uuid.uuid4()), "child_id": a_child, "started_at": "2026-10-02T07:00:00Z",
                   "duration_s": 115}
        first = rpc(stack, "sync_events", A.token, {"batch": [ev("brushing_recorded", payload, eid)]})
        again = rpc(stack, "sync_events", A.token, {"batch": [ev("brushing_recorded", payload, eid)]})
        c.check(first.ok and first.body[0]["status"] == "applied", "sync: first submission applied", str(first.body))
        c.check(again.ok and again.body == first.body, "sync: replay returns the identical result (idempotent)")
        r = rest(stack, "GET", f"brushing_event?id=eq.{payload['brushing_id']}&select=id", A.token)
        c.check(r.ok and len(r.body) == 1, "sync: replay wrote nothing twice")
        changed = dict(payload, duration_s=60)
        r = rpc(stack, "sync_events", A.token, {"batch": [ev("brushing_recorded", changed, eid)]})
        c.check(r.ok and r.body[0].get("code") == "event_id_conflict", "sync: same event_id, other payload -> conflict")
        r = rpc(stack, "sync_events", B.token, {"batch": [ev("brushing_recorded", payload, eid)]})
        c.check(r.ok and r.body[0].get("code") == "event_id_conflict", "sync: B replaying A's event_id -> conflict")
        r = rpc(stack, "sync_events", B.token, {"batch": [ev("brushing_recorded", dict(
            payload, brushing_id=str(uuid.uuid4())))]})
        c.check(r.ok and r.body[0].get("code") == "forbidden", "sync: B writing to A's child -> forbidden")
        r = rpc(stack, "sync_events", A.token, {"batch": []})
        c.check(r.status == 400, "sync: empty batch rejected (22023 -> 400)", f"HTTP {r.status}")

        # ------------------------------------------------------------- kader
        r = rest(stack, "POST", "class_roster", K.token,
                 body={"kader_id": K.id, "label": "Kelas 4A", "facility_code": "TST-HTTP-1"},
                 prefer="return=representation")
        roster = r.body[0]["id"] if r.ok and r.body else None
        c.check(roster is not None, "K: creates a roster for own facility", f"HTTP {r.status} {r.body}")
        r = rest(stack, "POST", "class_roster", K.token,
                 body={"kader_id": K.id, "label": "Kelas 5", "facility_code": "TST-OTHER-9"})
        c.check(r.status in (401, 403), "K: roster for another facility denied", f"HTTP {r.status}")
        r = rest(stack, "POST", "class_roster_member", K.token,
                 body={"roster_id": roster, "child_id": k_child, "member_label": "A-01"})
        c.check(r.status == 201, "K: adds own child to own roster", f"HTTP {r.status} {r.body}")
        r = rest(stack, "POST", "class_roster_member", K.token,
                 body={"roster_id": roster, "child_id": a_child, "member_label": "A-02"})
        c.check(r.status in (401, 403), "K: cannot pull A's child into the roster", f"HTTP {r.status}")
        r = rest(stack, "GET", "child_profile?select=id", K.token)
        c.check(r.ok and [x["id"] for x in r.body] == [k_child], "K: sees only own/roster children")
        r = rest(stack, "GET", "class_roster?select=id", A.token)
        c.check(r.ok and r.body == [], "A: cannot see K's roster")
        r = rest(stack, "POST", "class_roster", A.token, body={"kader_id": A.id, "label": "Kelas 1"})
        c.check(r.status in (401, 403), "A (not kader): cannot create a roster", f"HTTP {r.status}")

        # --------------------------------------------------- facility_viewer
        tot = 0
        for t in ("child_profile", "screening", "consent", "referral", "brushing_event", "image_upload",
                  "class_roster", "class_roster_member"):
            r = rest(stack, "GET", f"{t}?select=*", FV.token)
            tot += len(r.body) if r.ok and isinstance(r.body, list) else 999
        c.check(tot == 0, "FV: ZERO row-level child data over REST", f"rows={tot}")
        r = rpc(stack, "facility_screening_summary", FV.token, {"p_from": "2026-10-01", "p_to": "2026-10-31"})
        c.check(r.ok and all(x["facility_code"] == "TST-HTTP-1" for x in r.body)
                and all(x["suppressed"] and x["screenings"] is None for x in r.body),
                "FV: aggregates only own facility, small cells suppressed", f"HTTP {r.status} {r.body}")
        r = rpc(stack, "facility_screening_summary", K.token, {"p_from": "2026-10-01", "p_to": "2026-10-31"})
        c.check(r.status in (401, 403), "K: cannot call facility aggregates", f"HTTP {r.status}")

        # --------------------------------------------------------------- admin
        r = rest(stack, "GET", "child_profile?select=id", AD.token)
        c.check(r.ok and r.body == [], "AD: no direct row access to children")
        r = rpc(stack, "admin_get_child_record", AD.token, {"p_child": a_child, "p_reason_code": "support_ticket"})
        c.check(r.ok and r.body and r.body["child"]["birth_year"] == 2017, "AD: audited single-record RPC works")
        r = rest(stack, "GET", "audit_log?select=action,target_hash&action=eq.admin_child_read", AD.token)
        c.check(r.ok and len(r.body) >= 1 and all(a_child not in str(x) for x in r.body),
                "AD: read is audited with a hash (no raw id)")
        r = rpc(stack, "admin_grant_role", AD.token, {"p_user": A.id, "p_role": "facility_viewer",
                                                     "p_facility_code": "TST-HTTP-1"})
        c.check(r.status == 400, "AD: account owning child data cannot become facility_viewer", f"HTTP {r.status}")

        # ------------------------------------------------------------- storage
        base = f"{A.id}/{a_child}"
        name1 = f"{base}/{uuid.uuid4()}.jpg"
        r = storage_upload(stack, A.token, "dataset_photos", name1, tiny_jpeg())
        c.check(not r.ok, "storage: upload denied with flag OFF and no consent (c)", f"HTTP {r.status}")
        r = rpc(stack, "sync_events", A.token, {"batch": [ev("consent_changed", {
            "child_id": a_child, "consent_type": "c", "action": "grant", "text_version": "local-c-v1"})]})
        c.check(r.ok and r.body[0]["status"] == "applied", "A: grants consent (c) via sync")
        r = storage_upload(stack, A.token, "dataset_photos", name1, tiny_jpeg())
        c.check(not r.ok, "storage: consent (c) alone is not enough while the flag is OFF", f"HTTP {r.status}")
        r = rpc(stack, "admin_set_feature_flag", AD.token,
                {"p_key": "dataset_contribution", "p_enabled": True, "p_approval_ref": "LOCAL-TEST-ONLY"})
        c.check(r.ok, "AD: enables dataset_contribution (local test only, approval_ref required)", f"HTTP {r.status}")
        r = storage_upload(stack, A.token, "dataset_photos", name1, tiny_jpeg())
        ok_upload = r.ok
        if r.ok:
            uploaded.append(("dataset_photos", name1))
        c.check(r.ok, "storage: A uploads JPEG to own prefix (consent c + flag ON)", f"HTTP {r.status} {r.body}")
        r = storage_upload(stack, A.token, "dataset_photos", f"{B.id}/{a_child}/{uuid.uuid4()}.jpg", tiny_jpeg())
        c.check(not r.ok, "storage: A cannot write under B's prefix", f"HTTP {r.status}")
        r = storage_upload(stack, A.token, "dataset_photos", f"{A.id}/{b_child}/{uuid.uuid4()}.jpg", tiny_jpeg())
        c.check(not r.ok, "storage: A cannot upload a photo of B's child", f"HTTP {r.status}")
        r = storage_upload(stack, A.token, "dataset_photos", f"{base}/{uuid.uuid4()}.jpg", b"\x89PNG\r\n\x1a\n" + b"0" * 100,
                           content_type="image/png")
        c.check(not r.ok, "storage: non-JPEG mime type refused", f"HTTP {r.status}")
        r = storage_upload(stack, A.token, "dataset_photos", f"{base}/{uuid.uuid4()}.png", tiny_jpeg())
        c.check(not r.ok, "storage: non-.jpg object name refused", f"HTTP {r.status}")
        r = storage_upload(stack, A.token, "dataset_photos", f"{base}/budi_kelas4.jpg", tiny_jpeg())
        c.check(not r.ok, "storage: non-UUID file name refused", f"HTTP {r.status}")
        big = f"{base}/{uuid.uuid4()}.jpg"
        r = storage_upload(stack, A.token, "dataset_photos", big, tiny_jpeg(600 * 1024))
        if r.ok:
            uploaded.append(("dataset_photos", big))
        c.check(not r.ok, "storage: object > 500 KiB refused", f"HTTP {r.status}")
        r = storage_upload(stack, A.token, "dataset_photos", f"{base}/{uuid.uuid4()}.jpg", tiny_jpeg(500 * 1024 - 64))
        if r.ok:
            uploaded.append(("dataset_photos", r.body.get("Key", "").split("/", 1)[-1]))
        c.check(r.ok, "storage: object just under 500 KiB accepted", f"HTTP {r.status} {r.body}")
        r = storage_upload(stack, None, "dataset_photos", f"{base}/{uuid.uuid4()}.jpg", tiny_jpeg())
        c.check(not r.ok, "storage: anon upload refused", f"HTTP {r.status}")
        if ok_upload:
            r = storage_upload(stack, A.token, "dataset_photos", name1, tiny_jpeg(4096), upsert=True)
            c.check(not r.ok, "storage: A cannot overwrite (upsert) own upload", f"HTTP {r.status}")
            r = http(stack, "GET", f"/storage/v1/object/authenticated/dataset_photos/{name1}", token=A.token)
            c.check(not r.ok, "storage: A cannot download own upload", f"HTTP {r.status}")
            r = http(stack, "POST", "/storage/v1/object/list/dataset_photos", token=A.token,
                     body={"prefix": f"{A.id}/{a_child}", "limit": 100})
            c.check(r.ok and r.body == [] or r.status in (400, 403, 404), "storage: A cannot list the bucket",
                    f"HTTP {r.status} n={len(r.body) if isinstance(r.body, list) else '?'}")
            r = http(stack, "DELETE", "/storage/v1/object/dataset_photos", token=A.token, body={"prefixes": [name1]})
            still = http(stack, "HEAD", f"/storage/v1/object/authenticated/dataset_photos/{name1}",
                         token=stack.service_key, apikey=stack.service_key)
            c.check(still.ok, "storage: A's DELETE request leaves the object in place", f"del HTTP {r.status}")
            r = rpc(stack, "sync_events", A.token, {"batch": [ev("image_upload_registered", {
                "upload_id": name1.rsplit("/", 1)[1][:-4], "child_id": a_child, "object_path": name1})]})
            c.check(r.ok and r.body[0]["status"] == "applied", "sync: image metadata registered after the upload",
                    str(r.body))
            r = http(stack, "POST", f"/storage/v1/object/sign/dataset_photos/{name1}", token=A.token,
                     body={"expiresIn": 60})
            c.check(not r.ok, "storage: A cannot create a signed URL for the photo", f"HTTP {r.status}")
            r = http(stack, "POST", "/storage/v1/object/list/dataset_photos", token=stack.service_key,
                     apikey=stack.service_key, body={"prefix": f"{A.id}/{a_child}", "limit": 100})
            c.check(r.ok and len(r.body) >= 1, "storage: service_role (team PC) can list the dataset")
        r = http(stack, "GET", "/storage/v1/bucket/dataset_photos", token=A.token)
        c.check(not r.ok or r.body.get("public") is False, "storage: dataset_photos bucket is private")

        # manifests: public read, no client write
        mname = f"localcheck-{run}/manifest.json"
        r = storage_upload(stack, None, "manifests", mname, b'{"local": true}', "application/json", service=True)
        if r.ok:
            uploaded.append(("manifests", mname))
        c.check(r.ok, "manifests: service_role can publish", f"HTTP {r.status} {r.body}")
        r = http(stack, "GET", f"/storage/v1/object/public/manifests/{mname}", headers={})
        c.check(r.ok and r.raw == b'{"local": true}', "manifests: anonymous public read via URL", f"HTTP {r.status}")
        r = storage_upload(stack, None, "manifests", f"localcheck-{run}/evil.json", b"{}", "application/json")
        c.check(not r.ok, "manifests: anon cannot write", f"HTTP {r.status}")
        r = storage_upload(stack, A.token, "manifests", f"localcheck-{run}/evil.json", b"{}", "application/json")
        c.check(not r.ok, "manifests: guardian cannot write", f"HTTP {r.status}")
        r = storage_upload(stack, A.token, "manifests", mname, b"{}", "application/json", upsert=True)
        c.check(not r.ok, "manifests: guardian cannot overwrite", f"HTTP {r.status}")
        r = http(stack, "DELETE", "/storage/v1/object/manifests", token=A.token, body={"prefixes": [mname]})
        r2 = http(stack, "GET", f"/storage/v1/object/public/manifests/{mname}", headers={})
        c.check(r2.ok, "manifests: guardian cannot delete", f"del HTTP {r.status}")
        aname = f"localcheck-{run}/admin.json"
        r = storage_upload(stack, AD.token, "manifests", aname, b"{}", "application/json")
        if r.ok:
            uploaded.append(("manifests", aname))
        c.check(r.ok, "manifests: admin account can publish (policy)", f"HTTP {r.status} {r.body}")
    except CheckFailed as e:
        c.check(False, "unexpected setup failure", str(e))
    finally:
        # Clean up: flag OFF, objects removed through the Storage API, accounts deleted.
        rest(stack, "PATCH", "feature_flag?key=eq.dataset_contribution", None,
             body={"enabled": False, "approval_ref": None}, service=True)
        for bucket in {b for b, _ in uploaded}:
            names = [n for b, n in uploaded if b == bucket]
            http(stack, "DELETE", f"/storage/v1/object/{bucket}", token=stack.service_key, apikey=stack.service_key,
                 body={"prefixes": names})
        if not args.keep:
            for u in users.values():
                admin_delete_user(stack, u.id)
            left = psql(stack, "select count(*) from auth.users where email like 'localcheck-" + run + "-%';")
            c.check(left == "0", "cleanup: test accounts deleted", f"left={left}")
    return c.summary()


if __name__ == "__main__":
    sys.exit(main())
