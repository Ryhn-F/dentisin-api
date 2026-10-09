from __future__ import annotations

import base64
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from keepalive import ping as kp


def make_jwt(role: str) -> str:
    def enc(obj: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{enc({'alg': 'HS256'})}.{enc({'role': role})}.signaturesignature"


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class ScriptedOpener:
    """Returns/raises a scripted sequence and records the requests."""

    def __init__(self, *outcomes: int | Exception) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request, timeout: float = 0) -> FakeResponse:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome >= 400:
            raise urllib.error.HTTPError(request.full_url, outcome, "err", {}, None)  # type: ignore[arg-type]
        return FakeResponse(outcome)


def run_ping(opener: ScriptedOpener, **kwargs: Any) -> tuple[int, list[float], list[str]]:
    sleeps: list[float] = []
    logs: list[str] = []
    status = kp.ping(
        "https://x.supabase.co/rest/v1/rpc/keepalive",
        kwargs.pop("key", None),
        opener=opener,
        sleep=sleeps.append,
        log=logs.append,
        **kwargs,
    )
    return status, sleeps, logs


def test_2xx_is_success_without_retry() -> None:
    opener = ScriptedOpener(204)
    status, sleeps, _ = run_ping(opener)
    assert status == 204
    assert sleeps == []
    assert len(opener.requests) == 1


def test_missing_rpc_404_is_not_retried_and_not_success() -> None:
    opener = ScriptedOpener(404)
    status, sleeps, _ = run_ping(opener)
    assert status == 404
    assert len(opener.requests) == 1
    assert sleeps == []


def test_5xx_and_network_errors_are_retried_then_succeed() -> None:
    opener = ScriptedOpener(503, urllib.error.URLError("boom"), 200)
    status, sleeps, logs = run_ping(opener, backoff=1.0)
    assert status == 200
    assert sleeps == [1.0, 2.0]
    assert any("network error: URLError" in line for line in logs)


def test_gives_up_after_retries() -> None:
    opener = ScriptedOpener(500, 500, 500)
    status, _, _ = run_ping(opener)
    assert status == 500
    assert len(opener.requests) == 3


def test_all_network_failures_return_zero() -> None:
    opener = ScriptedOpener(TimeoutError(), TimeoutError(), OSError("x"))
    status, _, _ = run_ping(opener)
    assert status == 0


def test_headers_with_legacy_jwt_key() -> None:
    key = make_jwt("anon")
    opener = ScriptedOpener(200)
    run_ping(opener, key=key)
    req = opener.requests[0]
    assert req.get_method() == "POST"
    assert req.data == b"{}"
    assert req.get_header("Apikey") == key
    assert req.get_header("Authorization") == f"Bearer {key}"


def test_publishable_key_only_in_apikey_header() -> None:
    opener = ScriptedOpener(200)
    run_ping(opener, key="sb_publishable_" + "abcdefghij")
    req = opener.requests[0]
    assert req.get_header("Apikey") == "sb_publishable_" + "abcdefghij"
    assert req.get_header("Authorization") is None


def test_get_method_has_no_body() -> None:
    opener = ScriptedOpener(200)
    run_ping(opener, method="GET")
    assert opener.requests[0].get_method() == "GET"
    assert opener.requests[0].data is None


# --- configuration -----------------------------------------------------------------------


def test_main_requires_url(capsys: pytest.CaptureFixture[str]) -> None:
    assert kp.main([], {}) == kp.EXIT_CONFIG
    assert "KEEPALIVE_URL" in capsys.readouterr().err


@pytest.mark.parametrize("url", ["http://example.com/x", "ftp://x/y", "https:///nohost"])
def test_main_rejects_insecure_urls(url: str) -> None:
    assert kp.main([], {"KEEPALIVE_URL": url}) == kp.EXIT_CONFIG


@pytest.mark.parametrize(
    "key",
    [make_jwt("service_role"), "sb_secret_" + "abcdefghijkl"],
    ids=["service-role-jwt", "sb-secret-key"],
)
def test_main_refuses_privileged_key(key: str, capsys: pytest.CaptureFixture[str]) -> None:
    env = {"KEEPALIVE_URL": "https://x.supabase.co/rest/v1/rpc/keepalive", "SUPABASE_ANON_KEY": key}
    assert kp.main([], env) == kp.EXIT_CONFIG
    assert key not in capsys.readouterr().err


def test_main_refuses_key_equal_to_service_role_env() -> None:
    env = {
        "KEEPALIVE_URL": "https://x.supabase.co/rest/v1/rpc/keepalive",
        "SUPABASE_ANON_KEY": "same-opaque-key",
        "SUPABASE_SERVICE_ROLE_KEY": "same-opaque-key",
    }
    assert kp.main([], env) == kp.EXIT_CONFIG


def test_main_rejects_bad_method() -> None:
    assert kp.main(["--method", "DELETE"], {"KEEPALIVE_URL": "https://x.co/a"}) == kp.EXIT_CONFIG


# --- real HTTP against a local server (no mocks) -----------------------------------------


class _Handler(BaseHTTPRequestHandler):
    seen: list[dict[str, str]] = []
    status = 200

    def do_POST(self) -> None:  # noqa: N802
        type(self).seen.append({k.lower(): v for k, v in self.headers.items()})
        self.send_response(type(self).status)
        self.end_headers()

    def log_message(self, *args: object) -> None:
        return None


@pytest.fixture
def server() -> Iterator[str]:
    _Handler.seen = []
    _Handler.status = 200
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}/rest/v1/rpc/keepalive"
    httpd.shutdown()
    httpd.server_close()


def test_main_against_local_server(server: str, capsys: pytest.CaptureFixture[str]) -> None:
    key = make_jwt("anon")
    code = kp.main([], {"KEEPALIVE_URL": server, "SUPABASE_ANON_KEY": key})
    err = capsys.readouterr().err
    assert code == 0
    assert "OK (HTTP 200)" in err
    assert server not in err and key not in err
    assert _Handler.seen[0]["apikey"] == key


def test_main_reports_failure_from_local_server(server: str) -> None:
    _Handler.status = 401
    assert kp.main([], {"KEEPALIVE_URL": server}) == kp.EXIT_FAILED
