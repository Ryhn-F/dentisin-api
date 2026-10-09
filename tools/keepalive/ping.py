#!/usr/bin/env python3
"""Keep-alive ping for the Supabase Free project (stdlib only, runs on bare GitHub runners).

Supabase Free pauses a project after ~1 week without activity. A scheduled workflow
(`.github/workflows/keepalive.yml`) calls this script every ~3 days. The script sends one
light HTTP request to KEEPALIVE_URL (normally `<SUPABASE_URL>/rest/v1/rpc/keepalive`) and
treats any HTTP 2xx as success. It does not depend on the RPC existing yet.

Environment
  KEEPALIVE_URL        required, https URL (http only for localhost, used in tests)
  SUPABASE_ANON_KEY    optional, sent as `apikey` (+ `Authorization: Bearer` for JWT keys).
                       Anon/publishable key ONLY: a service_role/secret key is refused.

Exit codes: 0 success (2xx), 1 request failed, 2 configuration error.
The URL and keys are never printed.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import Any

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 2

Opener = Callable[..., Any]


class PingConfigError(Exception):
    pass


def _jwt_role(token: str) -> str | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except (ValueError, UnicodeDecodeError):
        return None
    role = data.get("role") if isinstance(data, dict) else None
    return role if isinstance(role, str) else None


def validate_key(key: str, service_role_env: str | None = None) -> None:
    """Refuse privileged keys. Only the anon/publishable key may be used for keep-alive."""
    if key.startswith("sb_secret_") or _jwt_role(key) == "service_role":
        raise PingConfigError("SUPABASE_ANON_KEY holds a privileged key; use the anon key only.")
    if service_role_env and key == service_role_env.strip():
        raise PingConfigError("SUPABASE_ANON_KEY equals SUPABASE_SERVICE_ROLE_KEY; refusing.")


def validate_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
        raise PingConfigError("KEEPALIVE_URL must be an https URL.")
    if not parsed.hostname:
        raise PingConfigError("KEEPALIVE_URL has no host.")


def build_request(url: str, key: str | None, method: str) -> urllib.request.Request:
    headers = {"User-Agent": "dentisai-keepalive/1", "Accept": "application/json"}
    data: bytes | None = None
    if method == "POST":
        headers["Content-Type"] = "application/json"
        data = b"{}"
    if key:
        headers["apikey"] = key
        if key.startswith("eyJ"):  # legacy JWT keys also go in the Authorization header
            headers["Authorization"] = f"Bearer {key}"
    return urllib.request.Request(url, data=data, headers=headers, method=method)  # noqa: S310


def ping(
    url: str,
    key: str | None = None,
    *,
    method: str = "POST",
    timeout: float = 20.0,
    retries: int = 3,
    backoff: float = 5.0,
    opener: Opener = urllib.request.urlopen,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = lambda msg: print(msg, file=sys.stderr),
) -> int:
    """Send the request, retrying on network errors and 5xx. Returns the final HTTP status
    (0 if no response was ever received)."""
    status = 0
    for attempt in range(1, retries + 1):
        request = build_request(url, key, method)
        try:
            with opener(request, timeout=timeout) as response:
                status = int(response.status)
        except urllib.error.HTTPError as exc:
            status = exc.code
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            status = 0
            log(f"keepalive: attempt {attempt}/{retries} network error: {type(exc).__name__}")
        else:
            log(f"keepalive: attempt {attempt}/{retries} -> HTTP {status}")
        if 200 <= status < 300:
            return status
        if status and status < 500:  # 4xx will not fix itself by retrying
            return status
        if attempt < retries:
            sleep(backoff * attempt)
    return status


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    env = os.environ if environ is None else environ
    args = list(sys.argv[1:] if argv is None else argv)
    method = "POST"
    if args and args[0] in {"--method", "-m"} and len(args) > 1:
        method = args[1].upper()
    if method not in {"GET", "POST"}:
        print("keepalive: --method must be GET or POST", file=sys.stderr)
        return EXIT_CONFIG
    url = env.get("KEEPALIVE_URL", "").strip()
    key = env.get("SUPABASE_ANON_KEY", "").strip() or None
    if not url:
        print(
            "keepalive: KEEPALIVE_URL is not set (add it as a GitHub Actions secret).",
            file=sys.stderr,
        )
        return EXIT_CONFIG
    try:
        validate_url(url)
        if key:
            validate_key(key, env.get("SUPABASE_SERVICE_ROLE_KEY"))
    except PingConfigError as exc:
        print(f"keepalive: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    status = ping(url, key, method=method)
    if 200 <= status < 300:
        print(f"keepalive: OK (HTTP {status})", file=sys.stderr)
        return EXIT_OK
    print(f"keepalive: FAILED (HTTP {status or 'no response'})", file=sys.stderr)
    return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
