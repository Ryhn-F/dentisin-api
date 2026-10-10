"""Cross-cutting behaviour: health, headers, CORS, size limit, rate limit,
error model, logging hygiene, inference switch, settings, OpenAPI freshness."""

from __future__ import annotations

import io
import json
import logging
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from app.logging_setup import JsonFormatter, configure_logging, redact
from app.ratelimit import TokenBucketLimiter
from app.repositories.memory import InMemoryRepository
from app.services.detector import Detection
from app.settings import Settings, get_settings
from fastapi import APIRouter
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from .conftest import (
    ADMIN,
    CHILD,
    GUARDIAN,
    IMG,
    IMG_NO_CONSENT,
    IMG_PATH,
    AppFactory,
    SigningKeys,
    TokenFactory,
    auth,
    make_settings,
    manifest_doc,
    sign_manifest,
)

API_ROOT = Path(__file__).resolve().parents[1]


def _code(resp: Any) -> str:
    return str(resp.json()["error"]["code"])


# --- health ------------------------------------------------------------------------------


def test_healthz(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200 and resp.json() == {"status": "ok"}


def test_readyz_ready_and_not_ready(
    app_factory: AppFactory, repo: InMemoryRepository, client: TestClient
) -> None:
    assert client.get("/readyz").json() == {"status": "ready"}
    repo.healthy = False
    resp = client.get("/readyz")
    assert resp.status_code == 503 and _code(resp) == "not_ready"
    repo.healthy = True
    with TestClient(app_factory(make_settings(SUPABASE_SERVICE_ROLE_KEY=""))) as c:
        resp = c.get("/readyz")
        assert resp.status_code == 503
        assert "SUPABASE" not in resp.text  # never reveals which setting is missing


# --- headers, docs, CORS -------------------------------------------------------------------


def test_security_headers_on_every_response(client: TestClient) -> None:
    for resp in (client.get("/healthz"), client.get("/nope"), client.get("/v1/ml/review-queue")):
        h = resp.headers
        assert h["x-content-type-options"] == "nosniff"
        assert h["x-frame-options"] == "DENY"
        assert h["referrer-policy"] == "no-referrer"
        assert "default-src 'none'" in h["content-security-policy"]
        assert h["cache-control"] == "no-store"
        assert "strict-transport-security" in h
        assert "server" not in h
        assert len(h["x-request-id"]) == 16


def test_docs_disabled_by_default(client: TestClient) -> None:
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_docs_can_be_enabled(app_factory: AppFactory) -> None:
    with TestClient(app_factory(make_settings(API_DOCS_ENABLED=True))) as c:
        assert c.get("/openapi.json").status_code == 200


def test_cors_closed_by_default(client: TestClient) -> None:
    resp = client.options(
        "/v1/ml/review-queue",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in resp.headers
    resp = client.get("/healthz", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in resp.headers


def test_cors_allowlist(app_factory: AppFactory) -> None:
    settings = make_settings(API_ALLOWED_ORIGINS="https://admin.example, https://b.example")
    with TestClient(app_factory(settings)) as c:
        ok = c.get("/healthz", headers={"Origin": "https://admin.example"})
        assert ok.headers["access-control-allow-origin"] == "https://admin.example"
        bad = c.get("/healthz", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in bad.headers


# --- error model ---------------------------------------------------------------------------


def test_unknown_route_and_method_use_error_model(client: TestClient) -> None:
    nf = client.get("/v1/does-not-exist")
    assert nf.status_code == 404 and _code(nf) == "not_found"
    assert set(nf.json()["error"]) == {"code", "message", "request_id"}
    mna = client.delete("/v1/ml/review-queue")
    assert mna.status_code == 405 and _code(mna) == "method_not_allowed"


def test_unhandled_exception_is_generic_500_without_trace(
    app_factory: AppFactory, caplog: pytest.LogCaptureFixture
) -> None:
    app = app_factory()
    router = APIRouter()

    @router.get("/boom")
    async def boom() -> None:
        raise RuntimeError(f"secret detail {IMG_PATH}")

    app.include_router(router)
    with caplog.at_level(logging.INFO), TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get("/boom")
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "internal_error"
    assert "secret" not in resp.text and "Traceback" not in resp.text
    assert "secret detail" not in caplog.text and IMG_PATH not in caplog.text


def test_upstream_failure_is_503(
    client: TestClient, make_token: TokenFactory, repo: InMemoryRepository
) -> None:
    from app.repositories.base import RepositoryError

    async def down(key: str) -> bool:
        raise RepositoryError(500)

    repo.flag_enabled = down  # type: ignore[method-assign]
    resp = client.get("/v1/ml/review-queue", headers=auth(make_token(ADMIN)))
    assert resp.status_code == 503 and _code(resp) == "upstream_unavailable"


# --- request size limit --------------------------------------------------------------------


def test_body_size_limit(app_factory: AppFactory, make_token: TokenFactory) -> None:
    with TestClient(app_factory(make_settings(API_MAX_BODY_BYTES=2048))) as c:
        big = {"image_upload_id": IMG, "pad": "x" * 5000}
        resp = c.post("/v1/ml/review-queue", json=big, headers=auth(make_token(ADMIN)))
        assert resp.status_code == 413 and _code(resp) == "payload_too_large"

        def chunks() -> Iterator[bytes]:
            for _ in range(10):
                yield b"x" * 1000

        streamed = c.post(
            "/v1/ml/review-queue",
            content=chunks(),
            headers={**auth(make_token(ADMIN)), "Content-Type": "application/json"},
        )
        assert streamed.status_code == 413
        bad_len = c.post(
            "/v1/ml/review-queue",
            content=b"{}",
            headers={"Content-Length": "abc", "Content-Type": "application/json"},
        )
        assert bad_len.status_code == 400


# --- rate limiting ------------------------------------------------------------------------


def test_rate_limit_per_ip(app_factory: AppFactory, make_token: TokenFactory) -> None:
    settings = make_settings(API_RATE_LIMIT_PER_MINUTE=1, API_RATE_LIMIT_BURST=3)
    with TestClient(app_factory(settings)) as c:
        statuses = [c.get("/v1/ml/review-queue").status_code for _ in range(5)]
        assert statuses[:3] == [401, 401, 401]
        assert statuses[3:] == [429, 429]
        limited = c.get("/v1/ml/review-queue")
        assert _code(limited) == "rate_limited" and int(limited.headers["retry-after"]) >= 1
        # health endpoints are never throttled (container probes)
        assert all(c.get("/healthz").status_code == 200 for _ in range(10))


def test_rate_limit_per_user(app_factory: AppFactory, make_token: TokenFactory) -> None:
    from app.deps import AppState

    app = app_factory()
    state: AppState = app.state.dentisai
    state.user_limiter = TokenBucketLimiter(1, 2)
    with TestClient(app) as c:
        token = make_token(ADMIN)
        codes = [c.get("/v1/ml/review-queue", headers=auth(token)).status_code for _ in range(3)]
        assert codes == [200, 200, 429]


def test_forwarded_for_only_when_trusted(app_factory: AppFactory) -> None:
    settings = make_settings(
        API_RATE_LIMIT_PER_MINUTE=1, API_RATE_LIMIT_BURST=1, API_TRUST_FORWARDED_FOR=True
    )
    with TestClient(app_factory(settings)) as c:
        a = c.get("/v1/x", headers={"X-Forwarded-For": "203.0.113.1, 10.0.0.1"})
        b = c.get("/v1/x", headers={"X-Forwarded-For": "203.0.113.2"})
        a2 = c.get("/v1/x", headers={"X-Forwarded-For": "203.0.113.1"})
        assert (a.status_code, b.status_code, a2.status_code) == (404, 404, 429)
    untrusted = make_settings(API_RATE_LIMIT_PER_MINUTE=1, API_RATE_LIMIT_BURST=1)
    with TestClient(app_factory(untrusted)) as c:
        c.get("/v1/x", headers={"X-Forwarded-For": "203.0.113.1"})
        spoofed = c.get("/v1/x", headers={"X-Forwarded-For": "198.51.100.9"})
        assert spoofed.status_code == 429  # header ignored: same client bucket


def test_token_bucket_refill_and_eviction() -> None:
    now = [0.0]
    limiter = TokenBucketLimiter(60, 2, clock=lambda: now[0], max_keys=2)
    assert limiter.acquire("a") == (True, 0)
    assert limiter.acquire("a") == (True, 0)
    allowed, retry = limiter.acquire("a")
    assert not allowed and retry == 1
    now[0] += 1.0
    assert limiter.acquire("a")[0] is True
    limiter.acquire("b")
    limiter.acquire("c")  # evicts the oldest key ("a")
    assert "a" not in limiter._buckets


# --- logging hygiene -------------------------------------------------------------------------


def test_logs_contain_no_tokens_paths_or_ids(
    app_factory: AppFactory,
    make_token: TokenFactory,
    keys: SigningKeys,
    repo: InMemoryRepository,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = make_settings(MANIFEST_PUBLIC_KEY_B64=keys.ed_public_b64, ENABLE_CLOUD_INFER=True)
    token = make_token(ADMIN)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        with caplog.at_level(logging.DEBUG), TestClient(app_factory(settings)) as c:
            h = auth(token)
            queue_id = c.post(
                "/v1/ml/review-queue", json={"image_upload_id": IMG}, headers=h
            ).json()["item"]["id"]
            c.post("/v1/ml/review-queue", json={"image_upload_id": IMG_NO_CONSENT}, headers=h)
            c.get("/v1/ml/review-queue", params={"limit": 5}, headers=h)
            c.post(
                f"/v1/ml/review-queue/{queue_id}/review",
                json={
                    "grader_pseudonym": "drg-a1",
                    "decision": "no_finding",
                    "image_width": 10,
                    "image_height": 10,
                    "notes": "catatan rahasia",
                },
                headers=h,
            )
            c.post("/v1/ml/infer", json={"image_upload_id": IMG}, headers=h)
            c.post("/v1/ml/models", json={"manifest_json": "{}", "manifest_sig": "{}"}, headers=h)
            c.get("/v1/ml/review-queue", headers=auth(make_token(ADMIN, exp_delta=-10)))
            c.get("/v1/ml/review-queue", headers={"Authorization": f"Bearer {token}x"})
    finally:
        root.removeHandler(handler)
    text = caplog.text + stream.getvalue()
    assert "request" in text  # access lines were emitted
    forbidden_fragments = [
        token,
        token.split(".")[1],
        "Bearer ",
        IMG_PATH,
        GUARDIAN,
        CHILD,
        IMG,
        ADMIN,
        "catatan rahasia",
        "drg-a1",
    ]
    for fragment in forbidden_fragments:
        assert fragment not in text, fragment
    # route templates, not raw paths
    assert "/v1/ml/review-queue/{queue_id}/review" in stream.getvalue()
    for line in stream.getvalue().splitlines():
        json.loads(line)  # every line is valid JSON


def test_redact_and_formatter() -> None:
    raw = (
        "Bearer abc.def.ghi eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig sb_secret_abcDEF "
        f"{IMG_PATH} {CHILD}"
    )
    out = redact(raw)
    for fragment in ("abc.def.ghi", "eyJhbGci", "sb_secret_abcDEF", IMG_PATH, CHILD):
        assert fragment not in out
    record = logging.LogRecord("t", logging.ERROR, __file__, 1, "msg %s", (CHILD,), None)
    record.__dict__.update(
        {"route": f"/x/{CHILD}", "status": 200, "unlisted": "secret", "count": [1]}
    )
    try:
        raise ValueError(IMG_PATH)
    except ValueError:
        record.exc_info = sys.exc_info()
    line = json.loads(JsonFormatter().format(record))
    assert line["exc_type"] == "ValueError"
    assert CHILD not in json.dumps(line) and IMG_PATH not in json.dumps(line)
    assert "unlisted" not in line and line["status"] == 200


def test_configure_logging_disables_uvicorn_access() -> None:
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    try:
        configure_logging("INFO")
        assert logging.getLogger("uvicorn.access").disabled is True
        assert isinstance(root.handlers[0].formatter, JsonFormatter)
    finally:
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])
        logging.getLogger("uvicorn.access").disabled = False


# --- inference switch ---------------------------------------------------------------------


def test_infer_disabled_by_default_is_404_even_unauthenticated(
    client: TestClient, make_token: TokenFactory
) -> None:
    assert client.post("/v1/ml/infer", json={"image_upload_id": IMG}).status_code == 404
    resp = client.post(
        "/v1/ml/infer", json={"image_upload_id": IMG}, headers=auth(make_token(ADMIN))
    )
    assert resp.status_code == 404 and _code(resp) == "not_found"
    assert Settings().ENABLE_CLOUD_INFER is False


def test_infer_enabled(
    app_factory: AppFactory, make_token: TokenFactory, repo: InMemoryRepository
) -> None:
    settings = make_settings(ENABLE_CLOUD_INFER=True)
    with TestClient(app_factory(settings)) as c:
        ok = c.post("/v1/ml/infer", json={"image_upload_id": IMG}, headers=auth(make_token(ADMIN)))
        assert ok.status_code == 200
        body = ok.json()
        assert body["detector"] == "null" and body["detections"] == []
        assert "bukan diagnosis" in body["disclaimer"]
        assert repo.audit_log[-1].action == "ml_cloud_infer"
        denied = c.post(
            "/v1/ml/infer", json={"image_upload_id": IMG}, headers=auth(make_token(GUARDIAN))
        )
        assert denied.status_code == 403
        gated = c.post(
            "/v1/ml/infer",
            json={"image_upload_id": IMG_NO_CONSENT},
            headers=auth(make_token(ADMIN)),
        )
        assert gated.status_code == 409 and _code(gated) == "consent_inactive"
        raw = c.post(
            "/v1/ml/infer",
            json={"image_upload_id": IMG, "image_b64": "/9j/"},
            headers=auth(make_token(ADMIN)),
        )
        assert raw.status_code == 422  # raw photos are never accepted


def test_infer_with_custom_detector_and_oversized_photo(
    app_factory: AppFactory, make_token: TokenFactory, repo: InMemoryRepository
) -> None:
    class FakeDetector:
        name = "fake"
        model_version = "0.0.1"

        async def detect(self, image: bytes) -> list[Detection]:
            return [Detection("caries_primary", 1, 2, 30, 40, 0.42)]

    settings = make_settings(ENABLE_CLOUD_INFER=True)
    with TestClient(app_factory(settings, detector=FakeDetector())) as c:
        resp = c.post(
            "/v1/ml/infer", json={"image_upload_id": IMG}, headers=auth(make_token(ADMIN))
        )
        assert resp.json()["detections"][0]["cls"] == "caries_primary"
        repo.photos[IMG_PATH] = b"x" * 600_000
        big = c.post("/v1/ml/infer", json={"image_upload_id": IMG}, headers=auth(make_token(ADMIN)))
        assert big.status_code == 409 and _code(big) == "photo_not_processable"


# --- settings ------------------------------------------------------------------------------


def test_settings_parsing_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_ALLOWED_ORIGINS", "https://a.example, ,https://b.example")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co/")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "secret-value")
    s = Settings()
    assert s.API_ALLOWED_ORIGINS == ["https://a.example", "https://b.example"]
    assert s.SUPABASE_URL == "https://x.supabase.co"
    assert s.jwt_issuer == "https://x.supabase.co/auth/v1"
    assert "secret-value" not in repr(s)
    assert s.missing_for_readiness() == []
    with pytest.raises(ValidationError):
        Settings(SUPABASE_URL="http://insecure.example")
    assert Settings(
        API_JWKS_ENABLED=False, SUPABASE_URL="", SUPABASE_SERVICE_ROLE_KEY=SecretStr("")
    ).missing_for_readiness() == [
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_JWT_SECRET",
    ]
    get_settings.cache_clear()
    assert get_settings().SUPABASE_URL == "https://x.supabase.co"
    get_settings.cache_clear()


def test_module_level_app_builds_with_supabase_repository() -> None:
    import app.main as main_module
    from app.repositories.supabase import SupabaseRepository

    built = main_module.create_app(make_settings(), configure_logs=False)
    assert isinstance(built.state.dentisai.repo, SupabaseRepository)
    with TestClient(built) as c:
        assert c.get("/healthz").status_code == 200


# --- OpenAPI ---------------------------------------------------------------------------


def test_openapi_file_is_fresh() -> None:
    from scripts.export_openapi import OUTPUT, render

    assert OUTPUT.exists(), "run: uv run python scripts/export_openapi.py"
    assert OUTPUT.read_text(encoding="utf-8") == render(), (
        "openapi.json is stale; run: uv run python scripts/export_openapi.py"
    )


def test_openapi_script_check_mode() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/export_openapi.py", "--check"],
        cwd=API_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_openapi_contents() -> None:
    from scripts.export_openapi import render

    doc = json.loads(render())
    assert set(doc["paths"]) == {
        "/healthz",
        "/readyz",
        "/v1/ml/review-queue",
        "/v1/ml/review-queue/{queue_id}/review",
        "/v1/ml/models",
        "/v1/ml/infer",
    }
    assert "HTTPBearer" in doc["components"]["securitySchemes"]
    # no CRUD verbs anywhere
    verbs = {v for p in doc["paths"].values() for v in p}
    assert verbs <= {"get", "post"}


def test_model_registration_happy_path_via_signed_doc(
    client: TestClient, make_token: TokenFactory, keys: SigningKeys
) -> None:
    from app.services.manifest_verify import key_id_of

    kid = key_id_of(keys.ed_private.public_key().public_bytes_raw())
    manifest, sig = sign_manifest(manifest_doc(kid), keys.ed_private)
    resp = client.post(
        "/v1/ml/models",
        json={"manifest_json": manifest, "manifest_sig": sig},
        headers=auth(make_token(ADMIN)),
    )
    assert resp.status_code == 201
