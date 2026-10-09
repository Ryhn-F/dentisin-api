"""Model/rules registration: signature-verified, idempotent, rollback-safe."""

from __future__ import annotations

import json
from typing import Any

import pytest
from app.repositories.memory import InMemoryRepository
from app.services.manifest_verify import key_id_of
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from .conftest import (
    ADMIN,
    VECTORS_DIR,
    AppFactory,
    SigningKeys,
    TokenFactory,
    auth,
    make_settings,
    manifest_doc,
    seeded_repo,
    sign_manifest,
)

M = "/v1/ml/models"


def _kid(keys: SigningKeys) -> str:
    return key_id_of(keys.ed_private.public_key().public_bytes_raw())


def _post(client: TestClient, token: str, manifest: str, sig: str) -> Any:
    return client.post(
        M, json={"manifest_json": manifest, "manifest_sig": sig}, headers=auth(token)
    )


def _code(resp: Any) -> str:
    return str(resp.json()["error"]["code"])


def test_register_valid_manifest(
    client: TestClient, make_token: TokenFactory, keys: SigningKeys, repo: InMemoryRepository
) -> None:
    manifest, sig = sign_manifest(manifest_doc(_kid(keys), sequence=3), keys.ed_private)
    resp = _post(client, make_token(ADMIN), manifest, sig)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["created"] is True
    assert body["model"]["sequence"] == 3
    assert body["model"]["model_version"] == "0.1.0"
    assert body["model"]["rules_version"] == "2026.10.1"
    assert body["model"]["signer_key_id"] == _kid(keys)
    assert body["model"]["channel"] == "production"
    assert len(repo.models) == 1
    assert repo.audit_log[-1].action == "ml_model_registered"

    again = _post(client, make_token(ADMIN), manifest, sig)
    assert again.status_code == 200 and again.json()["created"] is False
    assert len(repo.models) == 1


def test_metadata_comes_from_manifest_not_client(
    client: TestClient, make_token: TokenFactory, keys: SigningKeys
) -> None:
    manifest, sig = sign_manifest(manifest_doc(_kid(keys)), keys.ed_private)
    resp = client.post(
        M,
        json={"manifest_json": manifest, "manifest_sig": sig, "model_version": "9.9.9"},
        headers=auth(make_token(ADMIN)),
    )
    assert resp.status_code == 422  # extra fields are refused, nothing is trusted


def test_tampered_manifest_rejected(
    client: TestClient, make_token: TokenFactory, keys: SigningKeys, repo: InMemoryRepository
) -> None:
    manifest, sig = sign_manifest(manifest_doc(_kid(keys)), keys.ed_private)
    tampered = manifest.replace('"version":"0.1.0"', '"version":"0.1.1"')
    assert tampered != manifest
    resp = _post(client, make_token(ADMIN), tampered, sig)
    assert resp.status_code == 422 and _code(resp) == "E_SIGNATURE"
    assert repo.models == {}


def test_wrong_key_rejected(
    client: TestClient, make_token: TokenFactory, repo: InMemoryRepository
) -> None:
    attacker = Ed25519PrivateKey.generate()
    kid = key_id_of(attacker.public_key().public_bytes_raw())
    manifest, sig = sign_manifest(manifest_doc(kid), attacker)
    resp = _post(client, make_token(ADMIN), manifest, sig)
    assert resp.status_code == 422 and _code(resp) == "E_KEY_UNKNOWN"
    assert repo.models == {}


def test_wrong_key_claiming_trusted_key_id_rejected(
    client: TestClient, make_token: TokenFactory, keys: SigningKeys
) -> None:
    attacker = Ed25519PrivateKey.generate()
    manifest, sig = sign_manifest(manifest_doc(_kid(keys)), attacker, key_id=_kid(keys))
    resp = _post(client, make_token(ADMIN), manifest, sig)
    assert resp.status_code == 422 and _code(resp) == "E_SIGNATURE"


def test_revoked_key_rejected(
    app_factory: AppFactory, make_token: TokenFactory, keys: SigningKeys
) -> None:
    settings = make_settings(
        MANIFEST_PUBLIC_KEY_B64=keys.ed_public_b64, MANIFEST_REVOKED_KEY_IDS=_kid(keys)
    )
    manifest, sig = sign_manifest(manifest_doc(_kid(keys)), keys.ed_private)
    with TestClient(app_factory(settings)) as c:
        resp = _post(c, make_token(ADMIN), manifest, sig)
    assert resp.status_code == 422 and _code(resp) == "E_KEY_REVOKED"


def test_rollback_and_equivocation_rejected(
    client: TestClient, make_token: TokenFactory, keys: SigningKeys
) -> None:
    admin = make_token(ADMIN)
    kid = _kid(keys)
    assert (
        _post(client, admin, *sign_manifest(manifest_doc(kid, 5), keys.ed_private)).status_code
        == 201
    )
    older = _post(client, admin, *sign_manifest(manifest_doc(kid, 4), keys.ed_private))
    assert older.status_code == 409 and _code(older) == "E_ROLLBACK"
    other_bytes = manifest_doc(kid, 5, min_app_version="1.0.1")
    equivocation = _post(client, admin, *sign_manifest(other_bytes, keys.ed_private))
    assert equivocation.status_code == 409 and _code(equivocation) == "E_ROLLBACK"
    newer = _post(client, admin, *sign_manifest(manifest_doc(kid, 6), keys.ed_private))
    assert newer.status_code == 201


def test_concurrent_duplicate_sequence_maps_to_rollback(
    client: TestClient, make_token: TokenFactory, keys: SigningKeys, repo: InMemoryRepository
) -> None:
    from app.repositories.base import DuplicateError

    async def dup(model: Any) -> Any:
        raise DuplicateError

    repo.insert_model = dup  # type: ignore[method-assign]
    resp = _post(
        client, make_token(ADMIN), *sign_manifest(manifest_doc(_kid(keys)), keys.ed_private)
    )
    assert resp.status_code == 409 and _code(resp) == "E_ROLLBACK"


def test_production_rejects_internal_test_channel_and_placeholder(
    app_factory: AppFactory, make_token: TokenFactory, keys: SigningKeys
) -> None:
    settings = make_settings(MANIFEST_PUBLIC_KEY_B64=keys.ed_public_b64, API_ENV="production")
    kid = _kid(keys)
    with TestClient(app_factory(settings)) as c:
        internal = _post(
            c,
            make_token(ADMIN),
            *sign_manifest(manifest_doc(kid, channel="internal-test"), keys.ed_private),
        )
        assert _code(internal) == "E_CHANNEL"
        doc = manifest_doc(kid)
        model = doc["model"]
        assert isinstance(model, dict)
        model["license"] = "PLACEHOLDER"
        placeholder = _post(c, make_token(ADMIN), *sign_manifest(doc, keys.ed_private))
        assert _code(placeholder) == "E_PLACEHOLDER"


def test_non_canonical_and_schema_errors(
    client: TestClient, make_token: TokenFactory, keys: SigningKeys
) -> None:
    admin = make_token(ADMIN)
    kid = _kid(keys)
    doc = manifest_doc(kid)
    pretty = json.dumps(doc, indent=1)
    sig = sign_manifest(doc, keys.ed_private)[1]
    # pretty bytes signed properly -> still not canonical
    signed_pretty = keys.ed_private.sign(pretty.encode())
    import base64

    sig_pretty = (
        '{"alg":"Ed25519","key_id":"'
        + kid
        + '","sig":"'
        + base64.b64encode(signed_pretty).decode()
        + '"}'
    )
    resp = _post(client, admin, pretty, sig_pretty)
    assert resp.status_code == 422 and _code(resp) == "E_NOT_CANONICAL"
    # unknown top-level field
    bad = _post(client, admin, *sign_manifest({**doc, "extra": 1}, keys.ed_private))
    assert _code(bad) == "E_SCHEMA"
    # malformed sig file
    assert (
        _code(_post(client, admin, sign_manifest(doc, keys.ed_private)[0], "{}")) == "E_SIG_FORMAT"
    )
    # non-ascii manifest text
    resp = _post(client, admin, '{"a":"é"}', sig)
    assert resp.status_code == 422 and _code(resp) == "E_NOT_CANONICAL"


def test_registration_without_trusted_key_is_503(
    app_factory: AppFactory, make_token: TokenFactory, keys: SigningKeys
) -> None:
    for value in ("", "not-base64!!"):
        settings = make_settings(MANIFEST_PUBLIC_KEY_B64=value)
        with TestClient(app_factory(settings)) as c:
            resp = _post(
                c, make_token(ADMIN), *sign_manifest(manifest_doc(_kid(keys)), keys.ed_private)
            )
        assert resp.status_code == 503 and _code(resp) == "manifest_trust_not_configured"


def test_size_caps(client: TestClient, make_token: TokenFactory) -> None:
    resp = _post(client, make_token(ADMIN), "x" * (16 * 1024 + 1), "{}")
    assert resp.status_code == 422
    resp = _post(client, make_token(ADMIN), "{}", "x" * 513)
    assert resp.status_code == 422


# --- vector parity with backend/tools/manifest ------------------------------------------


@pytest.mark.skipif(not VECTORS_DIR.is_dir(), reason="manifest vectors not present")
def test_vectors_registration_end_to_end(app_factory: AppFactory, make_token: TokenFactory) -> None:
    vector = json.loads((VECTORS_DIR / "manifest_v1_valid_01_minimal.json").read_text("utf-8"))
    settings = make_settings(MANIFEST_PUBLIC_KEY_B64=vector["signing_key"]["public_key_b64"])
    with TestClient(app_factory(settings)) as c:
        resp = _post(c, make_token(ADMIN), vector["manifest_json"], vector["manifest_sig"])
        assert resp.status_code == 201, resp.text
        assert resp.json()["model"]["manifest_sha256"] == vector["canonical_sha256"]
    prod = make_settings(
        MANIFEST_PUBLIC_KEY_B64=vector["signing_key"]["public_key_b64"], API_ENV="production"
    )
    with TestClient(app_factory(prod, repo=seeded_repo())) as c:
        resp = _post(c, make_token(ADMIN), vector["manifest_json"], vector["manifest_sig"])
        assert _code(resp) == "E_KEY_TEST_ONLY"  # public TEST key never accepted in production
