"""Unit edges of the verifier that the vectors do not exercise."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from app.services import manifest_verify as mv
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .conftest import manifest_doc, sign_manifest

NOW = datetime(2026, 10, 9, tzinfo=UTC)


def _setup(**overrides: Any) -> tuple[bytes, bytes, mv.TrustStore]:
    key = Ed25519PrivateKey.generate()
    kid = mv.key_id_of(key.public_key().public_bytes_raw())
    doc = manifest_doc(kid)
    doc.update(overrides)
    manifest, sig = sign_manifest(doc, key)
    trust = mv.TrustStore({kid: key.public_key()})
    return manifest.encode(), sig.encode(), trust


def _code(raw: bytes, sig: bytes, trust: mv.TrustStore, **policy: Any) -> str:
    try:
        mv.verify_manifest(raw, sig, trust, mv.Policy(now=NOW, **policy))
    except mv.ManifestError as exc:
        return exc.code
    return "OK"


def test_ok_and_products_triple() -> None:
    raw, sig, trust = _setup()
    assert _code(raw, sig, trust) == "OK"
    base = "https://testproj.supabase.co/storage/v1/object/public/manifests"
    rec = {
        "version": "1.0.0",
        "url": f"{base}/r/rules.json",
        "sha256": "b" * 64,
        "size_bytes": 10,
        "products_url": f"{base}/r/products.json",
        "products_sha256": "c" * 64,
        "products_size_bytes": 10,
    }
    raw, sig, trust = _setup(recommender=rec)
    assert _code(raw, sig, trust) == "OK"
    partial = {k: v for k, v in rec.items() if k != "products_sha256"}
    raw, sig, trust = _setup(recommender=partial)
    assert _code(raw, sig, trust) == "E_SCHEMA"


@pytest.mark.parametrize(
    "overrides",
    [
        {"sequence": 0},
        {"sequence": True},
        {"created_at": "2026-10-08 00:00:00"},
        {"created_at": "2026-02-30T00:00:00Z"},
        {"expires_at": "2026-10-07T00:00:00Z"},
        {"min_app_version": "1.0"},
        {"signer_key_id": "XYZ"},
        {"channel": "beta"},
        {"model": "x"},
        {"rules": {"version": "1"}},
        {"channel": 5},
    ],
)
def test_schema_errors(overrides: dict[str, Any]) -> None:
    raw, sig, trust = _setup(**overrides)
    assert _code(raw, sig, trust) in {"E_SCHEMA", "E_KEY_ID_MISMATCH"}


@pytest.mark.parametrize(
    ("component", "field", "value"),
    [
        ("model", "version", "-bad"),
        ("model", "license", ""),
        ("rules", "url", "http://insecure.example/x"),
        ("rules", "url", "https://h.example/a//b"),
        ("rules", "sha256", "ABC"),
        ("rules", "size_bytes", 3 * 1024 * 1024),
    ],
)
def test_component_errors(component: str, field: str, value: Any) -> None:
    key = Ed25519PrivateKey.generate()
    kid = mv.key_id_of(key.public_key().public_bytes_raw())
    doc = manifest_doc(kid)
    comp = doc[component]
    assert isinstance(comp, dict)
    comp[field] = value
    manifest, sig = sign_manifest(doc, key)
    trust = mv.TrustStore({kid: key.public_key()})
    assert _code(manifest.encode(), sig.encode(), trust) == "E_SCHEMA"


def test_size_caps_and_sig_format() -> None:
    raw, sig, trust = _setup()
    assert _code(b"x" * (16 * 1024 + 1), sig, trust) == "E_TOO_LARGE"
    assert _code(raw, b"x" * 513, trust) == "E_TOO_LARGE"
    assert _code(raw, b'["a"]', trust) == "E_SIG_FORMAT"
    assert _code(raw, b'{"alg":"Ed25519","key_id":"zz","sig":"AA=="}', trust) == "E_SIG_FORMAT"
    assert _code(raw, b'{"alg":"Ed25519","key_id":"0011223344556677","sig":5}', trust) == (
        "E_SIG_FORMAT"
    )
    with pytest.raises(mv.ManifestError):
        mv.parse_sig_file(b"x" * 600)


def test_policy_paths() -> None:
    raw, sig, trust = _setup(expires_at="2026-10-09T00:00:00Z")
    assert _code(raw, sig, trust) == "E_EXPIRED"
    raw, sig, trust = _setup()
    assert _code(raw, sig, trust, app_version="0.9.0") == "E_APP_TOO_OLD"
    assert _code(raw, sig, trust, last_schema_version=2) == "E_SCHEMA_DOWNGRADE"
    with pytest.raises(ValueError, match="MAJOR"):
        mv.parse_app_version("1.0")


def test_canonical_rejections() -> None:
    for text in ("[1.5]", "[1e3]", "[NaN]", '{"A":1}', '["é"]', "[-0]", "{", "[01]"):
        with pytest.raises(mv.ManifestError):
            mv.parse_strict(text)
    with pytest.raises(mv.ManifestError):
        mv.canonicalize([2**53])
    with pytest.raises(mv.ManifestError):
        mv.canonicalize({"k": 1.5})  # type: ignore[dict-item]
    deep: Any = 1
    for _ in range(20):
        deep = [deep]
    with pytest.raises(mv.ManifestError):
        mv.canonicalize(deep)
    with pytest.raises(mv.ManifestError):
        mv.parse_canonical(b"[1]\n")
    with pytest.raises(mv.ManifestError):
        mv.parse_strict("[" * 100_000 + "]" * 100_000)
    assert mv.canonicalize([True, False, None, 'a\\"b']) == b'[true,false,null,"a\\\\\\"b"]'
    assert mv.contains_placeholder(["x", {"y": "PLACEHOLDER"}]) is True
    assert mv.contains_placeholder([1, None]) is False


def test_trust_store_rejects_bad_keys() -> None:
    with pytest.raises(ValueError, match="base64"):
        mv.TrustStore.from_b64(["AAAA"])
    with pytest.raises(ValueError, match="base64"):
        mv.TrustStore.from_b64(["***"])
