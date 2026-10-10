"""Shared helpers for the LOCAL-ONLY Supabase HTTP checks (stdlib only).

Used by ``rls_http_check.py`` and ``deletion_e2e_check.py``. Everything here talks
to the stack started by ``supabase start`` (Kong on 127.0.0.1:54321, Postgres in
the ``supabase_db_<project_id>`` container) and refuses anything else:

* the API URL must be http(s)://localhost|127.0.0.1|[::1];
* the anon / service_role keys must be the local demo JWTs (``iss`` =
  ``supabase-demo``) — a hosted project's keys carry ``iss = supabase`` + ``ref``
  and are rejected, so a pasted production key cannot be used by mistake;
* SQL inspection goes through ``docker exec`` into the local DB container only.

Keys come from the environment (``ANON_KEY`` / ``SERVICE_ROLE_KEY`` as printed by
``supabase status -o env``) or, when absent, from ``supabase status -o env`` run in
the backend folder. No value is ever printed.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[2]
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
# Cloudflare Turnstile documented always-pass dummy token. Only works when the
# LOCAL GoTrue runs with the Turnstile *test* secret (see supabase/README.md).
TURNSTILE_DUMMY_TOKEN = "XXXX.DUMMY.TOKEN.XXXX"


class CheckFailed(AssertionError):
    pass


# ---------------------------------------------------------------------------
# Safety guards
# ---------------------------------------------------------------------------
def assert_local_url(url: str) -> str:
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https") or (p.hostname or "").lower() not in LOCAL_HOSTS:
        sys.exit(f"REFUSED: {url!r} is not a local Supabase URL (only localhost/127.0.0.1/[::1]).")
    return url.rstrip("/")


def _jwt_payload(token: str) -> dict[str, Any]:
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:  # noqa: BLE001
        return {}


def assert_local_key(token: str, expected_role: str) -> str:
    claims = _jwt_payload(token)
    if claims.get("iss") != "supabase-demo" or "ref" in claims or claims.get("role") != expected_role:
        sys.exit(f"REFUSED: the {expected_role} key is not a local demo key (iss must be 'supabase-demo').")
    return token


def _status_env() -> dict[str, str]:
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if not npx:
        return {}
    r = subprocess.run([npx, "--yes", "supabase@latest", "status", "-o", "env"], cwd=BACKEND_DIR,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out: dict[str, str] = {}
    for line in r.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


@dataclass
class Stack:
    url: str
    anon_key: str
    service_key: str
    db_container: str

    @classmethod
    def from_env(cls, url: str | None = None) -> "Stack":
        env = dict(os.environ)
        if not env.get("ANON_KEY") or not env.get("SERVICE_ROLE_KEY"):
            env.update({k: v for k, v in _status_env().items() if k not in env or not env[k]})
        api = assert_local_url(url or env.get("API_URL") or "http://127.0.0.1:54321")
        anon = env.get("ANON_KEY", "")
        service = env.get("SERVICE_ROLE_KEY", "")
        if not anon or not service:
            sys.exit("Missing ANON_KEY / SERVICE_ROLE_KEY: run `eval \"$(npx supabase status -o env)\"` first.")
        project = env.get("LOCAL_PROJECT_ID", "dentisai")
        return cls(api, assert_local_key(anon, "anon"), assert_local_key(service, "service_role"),
                   f"supabase_db_{project}")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
@dataclass
class Resp:
    status: int
    body: Any
    raw: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


def http(stack: Stack, method: str, path: str, *, token: str | None = None, apikey: str | None = None,
         body: Any = None, raw: bytes | None = None, headers: dict[str, str] | None = None) -> Resp:
    url = stack.url + path
    assert_local_url(url)
    h = {"apikey": apikey or stack.anon_key}
    if token:
        h["Authorization"] = f"Bearer {token}"
    data = None
    if raw is not None:
        data = raw
    elif body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            content, status, hdrs = r.read(), r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        content, status, hdrs = e.read(), e.code, dict(e.headers)
    try:
        parsed = json.loads(content) if content else None
    except ValueError:
        parsed = content.decode("utf-8", "replace")
    return Resp(status, parsed, content, hdrs)


# ---------------------------------------------------------------------------
# Auth (local GoTrue)
# ---------------------------------------------------------------------------
@dataclass
class User:
    label: str
    id: str
    email: str
    password: str
    token: str = ""


def new_password() -> str:
    # Satisfies minimum_password_length = 10 + lower/upper/digits. Random per run.
    return "Lc" + uuid.uuid4().hex[:14] + "9Z"


def admin_create_user(stack: Stack, label: str, run_id: str) -> User:
    email = f"localcheck-{run_id}-{label}@example.com"
    pw = new_password()
    r = http(stack, "POST", "/auth/v1/admin/users", token=stack.service_key, apikey=stack.service_key,
             body={"email": email, "password": pw, "email_confirm": True})
    if not r.ok:
        raise CheckFailed(f"admin create user {label}: HTTP {r.status} {r.body}")
    return User(label, r.body["id"], email, pw)


def sign_in(stack: Stack, user: User) -> str:
    """Real GoTrue password grant (with the Turnstile dummy token); falls back to an
    admin magic link + /verify when CAPTCHA verification is unreachable offline."""
    r = http(stack, "POST", "/auth/v1/token?grant_type=password",
             body={"email": user.email, "password": user.password,
                   "gotrue_meta_security": {"captcha_token": TURNSTILE_DUMMY_TOKEN}})
    if r.ok:
        user.token = r.body["access_token"]
        return "password"
    link = http(stack, "POST", "/auth/v1/admin/generate_link", token=stack.service_key, apikey=stack.service_key,
                body={"type": "magiclink", "email": user.email})
    if not link.ok:
        raise CheckFailed(f"sign-in {user.label}: password HTTP {r.status}, generate_link HTTP {link.status}")
    hashed = link.body.get("hashed_token") or link.body.get("properties", {}).get("hashed_token")
    v = http(stack, "POST", "/auth/v1/verify", body={"type": "magiclink", "token_hash": hashed})
    if not v.ok:
        raise CheckFailed(f"verify {user.label}: HTTP {v.status}")
    user.token = v.body["access_token"]
    return f"magiclink (password grant HTTP {r.status})"


def admin_delete_user(stack: Stack, user_id: str) -> None:
    http(stack, "DELETE", f"/auth/v1/admin/users/{user_id}", token=stack.service_key, apikey=stack.service_key)


# ---------------------------------------------------------------------------
# REST helpers
# ---------------------------------------------------------------------------
def rest(stack: Stack, method: str, path: str, token: str | None, body: Any = None,
         prefer: str | None = None, service: bool = False) -> Resp:
    hdr = {"Prefer": prefer} if prefer else {}
    if service:
        return http(stack, method, "/rest/v1/" + path, token=stack.service_key, apikey=stack.service_key,
                    body=body, headers=hdr)
    return http(stack, method, "/rest/v1/" + path, token=token, body=body, headers=hdr)


def rpc(stack: Stack, fn: str, token: str | None, args: dict[str, Any] | None = None, service: bool = False) -> Resp:
    return rest(stack, "POST", f"rpc/{fn}", token, body=args or {}, service=service)


def ev(event_type: str, payload: dict[str, Any], event_id: str | None = None) -> dict[str, Any]:
    return {"event_id": event_id or str(uuid.uuid4()), "type": event_type, "payload": payload}


def make_child_via_sync(stack: Stack, user: User, consents: tuple[str, ...] = ("a", "b")) -> str:
    """Creates a child the way the app does: consent_changed (grant) -> child_upserted."""
    child = str(uuid.uuid4())
    batch = [ev("consent_changed", {"child_id": child, "consent_type": c, "action": "grant",
                                    "text_version": "local-v1"}) for c in consents]
    batch.append(ev("child_upserted", {"child_id": child, "birth_year": 2017, "birth_month": 5, "allergies": []}))
    r = rpc(stack, "sync_events", user.token, {"batch": batch})
    if not r.ok or any(x.get("status") != "applied" for x in r.body):
        raise CheckFailed(f"make_child for {user.label}: HTTP {r.status} {r.body}")
    return child


# ---------------------------------------------------------------------------
# SQL through the LOCAL DB container (inspection only)
# ---------------------------------------------------------------------------
def psql(stack: Stack, sql: str, user: str = "postgres") -> str:
    docker = shutil.which("docker")
    if not docker:
        raise CheckFailed("docker not found")
    r = subprocess.run([docker, "exec", "-i", stack.db_container, "psql", "-X", "-q", "-t", "-A",
                        "-v", "ON_ERROR_STOP=1", "-U", user, "-d", "postgres"],
                       input=sql, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise CheckFailed(f"psql failed: {r.stderr.strip()[-400:]}")
    return r.stdout.strip()


def tiny_jpeg(size: int = 2048) -> bytes:
    """Synthetic JPEG-shaped bytes (SOI .. EOI), no real image content."""
    body = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    pad = max(0, size - len(body) - 2)
    return body + b"\x00" * pad + b"\xff\xd9"


class Checker:
    """Collects named assertions; prints one line each (no ids, e-mails or keys)."""

    def __init__(self) -> None:
        self.passed = 0
        self.failed: list[str] = []
        self.t0 = time.time()

    def check(self, cond: bool, name: str, detail: str = "") -> bool:
        if cond:
            self.passed += 1
            print(f"ok   {name}")
        else:
            self.failed.append(name)
            print(f"FAIL {name}" + (f"  [{detail}]" if detail else ""))
        return cond

    def summary(self) -> int:
        print(f"\n{self.passed} passed, {len(self.failed)} failed ({time.time() - self.t0:.1f}s)")
        return 1 if self.failed else 0
