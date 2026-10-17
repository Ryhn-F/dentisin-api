"""JWT verification: only valid Supabase user tokens pass; roles never come
from user_metadata."""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest
from app.auth import JwksCache, http_jwks_fetcher, parse_bearer, roles_from_app_metadata
from app.errors import ApiError
from app.repositories.memory import InMemoryRepository
from cryptography.hazmat.primitives.asymmetric.ec import SECP256R1
from cryptography.hazmat.primitives.asymmetric.ec import generate_private_key as ec_generate
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key as rsa_generate
from fastapi.testclient import TestClient

from .conftest import (
    ADMIN,
    GUARDIAN,
    HS_SECRET,
    NOROLE,
    AppFactory,
    SigningKeys,
    TokenFactory,
    auth,
    jwks_doc,
    make_settings,
)

LIST = "/v1/ml/review-queue"


def _b64url(data: dict[str, Any]) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _code(resp: httpx.Response) -> str:
    body: dict[str, Any] = resp.json()
    return str(body["error"]["code"])


def test_missing_token_401(client: TestClient) -> None:
    resp = client.get(LIST)
    assert resp.status_code == 401
    assert _code(resp) == "unauthorized"
    assert resp.headers["www-authenticate"].startswith("Bearer")


@pytest.mark.parametrize("header", ["Basic abc", "Bearer", "Token xyz", "bearer "])
def test_malformed_authorization_header_401(client: TestClient, header: str) -> None:
    resp = client.get(LIST, headers={"Authorization": header})
    assert resp.status_code == 401
    assert _code(resp) in {"invalid_token", "unauthorized"}


def test_valid_hs256_admin_token_ok(client: TestClient, make_token: TokenFactory) -> None:
    resp = client.get(LIST, headers=auth(make_token(ADMIN)))
    assert resp.status_code == 200


def test_valid_es256_jwks_token_ok(client: TestClient, make_token: TokenFactory) -> None:
    resp = client.get(LIST, headers=auth(make_token(ADMIN, alg="ES256")))
    assert resp.status_code == 200


def test_expired_token_rejected(client: TestClient, make_token: TokenFactory) -> None:
    resp = client.get(LIST, headers=auth(make_token(ADMIN, exp_delta=-3600)))
    assert resp.status_code == 401
    assert _code(resp) == "token_expired"


def test_forged_hs256_signature_rejected(client: TestClient, make_token: TokenFactory) -> None:
    forged = make_token(ADMIN, key="some-other-secret-that-is-long-enough-123")
    assert client.get(LIST, headers=auth(forged)).status_code == 401


def test_tampered_payload_rejected(client: TestClient, make_token: TokenFactory) -> None:
    token = make_token(GUARDIAN)
    header, _, sig = token.split(".")
    payload = _b64url({"sub": ADMIN, "aud": "authenticated", "exp": 9_999_999_999})
    assert client.get(LIST, headers=auth(f"{header}.{payload}.{sig}")).status_code == 401


def test_forged_es256_with_unknown_key_rejected(
    client: TestClient, make_token: TokenFactory
) -> None:
    attacker = ec_generate(SECP256R1())
    token = make_token(ADMIN, alg="ES256", key=attacker)
    assert client.get(LIST, headers=auth(token)).status_code == 401


def test_unknown_kid_rejected(client: TestClient, make_token: TokenFactory) -> None:
    token = make_token(ADMIN, alg="ES256", kid="not-in-jwks")
    assert client.get(LIST, headers=auth(token)).status_code == 401


def test_alg_mismatch_with_jwk_rejected(
    client: TestClient, make_token: TokenFactory, keys: SigningKeys
) -> None:
    # RS256 token claiming the EC key id: jwk alg (ES256) != header alg.
    token = make_token(ADMIN, alg="RS256", key=rsa_generate(65537, 2048), kid=keys.ec_kid)
    assert client.get(LIST, headers=auth(token)).status_code == 401


@pytest.mark.parametrize(
    ("kwargs", "label"),
    [
        ({"aud": "anon"}, "wrong aud"),
        ({"aud": None}, "missing aud"),
        ({"iss": "https://evil.example/auth/v1"}, "wrong iss"),
        ({"iss": None}, "missing iss"),
        ({"role": "anon"}, "anon role"),
        ({"role": "service_role"}, "service_role token"),
        ({"sub": "not-a-uuid"}, "non-uuid sub"),
        ({"extra": {"nbf": 9_999_999_999}}, "not yet valid"),
    ],
)
def test_claim_checks(
    client: TestClient, make_token: TokenFactory, kwargs: dict[str, Any], label: str
) -> None:
    args = dict(kwargs)
    sub = args.pop("sub", ADMIN)
    resp = client.get(LIST, headers=auth(make_token(sub, **args)))
    assert resp.status_code == 401, label


def test_missing_exp_rejected(client: TestClient) -> None:
    import jwt

    token = jwt.encode(
        {
            "sub": ADMIN,
            "aud": "authenticated",
            "iss": make_settings().jwt_issuer,
            "role": "authenticated",
        },
        HS_SECRET,
        algorithm="HS256",
    )
    assert client.get(LIST, headers=auth(token)).status_code == 401


def test_alg_none_rejected(client: TestClient) -> None:
    header = _b64url({"alg": "none", "typ": "JWT"})
    payload = _b64url(
        {
            "sub": ADMIN,
            "aud": "authenticated",
            "iss": make_settings().jwt_issuer,
            "role": "authenticated",
            "exp": 9_999_999_999,
        }
    )
    for token in (f"{header}.{payload}.", f"{header}.{payload}"):
        assert client.get(LIST, headers=auth(token)).status_code == 401


def test_hs256_rejected_when_no_secret_configured(
    app_factory: AppFactory, make_token: TokenFactory
) -> None:
    settings = make_settings(SUPABASE_JWT_SECRET="")
    with TestClient(app_factory(settings)) as c:
        assert c.get(LIST, headers=auth(make_token(ADMIN))).status_code == 401
        assert c.get(LIST, headers=auth(make_token(ADMIN, alg="ES256"))).status_code == 200


def test_key_confusion_hs256_signed_with_public_jwk_rejected(
    app_factory: AppFactory, keys: SigningKeys, make_token: TokenFactory
) -> None:
    settings = make_settings(SUPABASE_JWT_SECRET="")
    public_json = json.dumps(jwks_doc(keys)["keys"][0])
    token = make_token(ADMIN, key=public_json + "x" * 32)
    with TestClient(app_factory(settings)) as c:
        assert c.get(LIST, headers=auth(token)).status_code == 401


def test_jwks_disabled_rejects_asymmetric(
    app_factory: AppFactory, make_token: TokenFactory
) -> None:
    settings = make_settings(API_JWKS_ENABLED=False)
    with TestClient(app_factory(settings)) as c:
        assert c.get(LIST, headers=auth(make_token(ADMIN, alg="ES256"))).status_code == 401


def test_oversized_token_rejected(client: TestClient) -> None:
    assert client.get(LIST, headers=auth("a" * 9000)).status_code == 401


def test_garbage_token_rejected(client: TestClient) -> None:
    assert client.get(LIST, headers=auth("not.a.jwt")).status_code == 401


# --- roles ---------------------------------------------------------------------------


def test_user_metadata_role_is_ignored(client: TestClient, make_token: TokenFactory) -> None:
    token = make_token(NOROLE, user_metadata={"role": "admin", "roles": ["admin"]})
    assert client.get(LIST, headers=auth(token)).status_code == 403


def test_table_role_source_ignores_app_metadata(
    client: TestClient, make_token: TokenFactory
) -> None:
    # Default source = public.user_role; a claimed app_metadata role is not enough.
    token = make_token(NOROLE, app_metadata={"role": "admin"})
    assert client.get(LIST, headers=auth(token)).status_code == 403


def test_app_metadata_role_source(
    app_factory: AppFactory, make_token: TokenFactory, repo: InMemoryRepository
) -> None:
    settings = make_settings(API_ROLE_SOURCE="app_metadata")
    with TestClient(app_factory(settings)) as c:
        ok = make_token(NOROLE, app_metadata={"roles": ["admin"]})
        assert c.get(LIST, headers=auth(ok)).status_code == 200
        ok2 = make_token(NOROLE, app_metadata={"role": "admin"})
        assert c.get(LIST, headers=auth(ok2)).status_code == 200
        # user_metadata never counts, even in app_metadata mode
        bad = make_token(NOROLE, user_metadata={"role": "admin"}, app_metadata={"x": 1})
        assert c.get(LIST, headers=auth(bad)).status_code == 403
        # table roles are NOT consulted in this mode
        assert c.get(LIST, headers=auth(make_token(ADMIN))).status_code == 403


def test_anonymous_user_cannot_use_admin_endpoints(
    client: TestClient, make_token: TokenFactory
) -> None:
    token = make_token(ADMIN, extra={"is_anonymous": True})
    assert client.get(LIST, headers=auth(token)).status_code == 403


def test_roles_from_app_metadata_filters_garbage() -> None:
    claims = {"app_metadata": {"roles": ["admin", 3, "bad role!"], "role": "guardian"}}
    assert roles_from_app_metadata(claims) == frozenset({"admin", "guardian"})
    assert roles_from_app_metadata({"app_metadata": "admin"}) == frozenset()
    assert roles_from_app_metadata({}) == frozenset()


def test_parse_bearer() -> None:
    assert parse_bearer("Bearer abc") == "abc"
    for bad in (None, "", "Basic abc", "Bearer   "):
        with pytest.raises(ApiError):
            parse_bearer(bad)


# --- JWKS cache ----------------------------------------------------------------------


@pytest.mark.anyio
async def test_jwks_cache_ttl_and_unknown_kid_throttle(keys: SigningKeys) -> None:
    calls = 0
    now = [1000.0]

    async def fetch() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        doc = jwks_doc(keys)
        doc["keys"].append("garbage")
        doc["keys"].append({"kid": "enc", "use": "enc", "kty": "oct", "k": "AA"})
        doc["keys"].append({"kid": "bad", "kty": "unknown"})
        return doc

    cache = JwksCache(fetch, ttl_s=600, clock=lambda: now[0])
    assert await cache.get(keys.ec_kid) is not None
    assert await cache.get(keys.ec_kid) is not None
    assert calls == 1
    assert await cache.get("nope") is None  # within 30 s: no refetch
    assert calls == 1
    now[0] += 31
    assert await cache.get("nope") is None  # unknown kid after 30 s: one refetch
    assert calls == 2
    now[0] += 601
    await cache.get(keys.ec_kid)  # TTL expiry
    assert calls == 3


@pytest.mark.anyio
async def test_jwks_fetch_failure_is_contained(keys: SigningKeys) -> None:
    async def failing() -> dict[str, Any]:
        raise httpx.ConnectError("boom")

    cache = JwksCache(failing, ttl_s=600)
    assert await cache.get(keys.ec_kid) is None


@pytest.mark.anyio
async def test_http_jwks_fetcher(keys: SigningKeys) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["apikey"] = request.headers.get("apikey", "")
        return httpx.Response(200, json=jwks_doc(keys))

    settings = make_settings(SUPABASE_ANON_KEY="anon-key")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        doc = await http_jwks_fetcher(http, settings)()
    assert doc["keys"][0]["kid"] == keys.ec_kid
    assert seen["url"] == "https://testproj.supabase.co/auth/v1/.well-known/jwks.json"
    assert seen["apikey"] == "anon-key"

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[1]))
    ) as http:
        with pytest.raises(ValueError, match="jwks"):
            await http_jwks_fetcher(http, make_settings())()


def test_verifier_rejects_when_supabase_url_missing(
    app_factory: AppFactory, make_token: TokenFactory
) -> None:
    settings = make_settings(SUPABASE_URL="")
    with TestClient(app_factory(settings)) as c:
        assert c.get(LIST, headers=auth(make_token(ADMIN))).status_code == 401
