"""Library-level sign/verify behaviour (SPEC.md section 5)."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import timedelta

import pytest

from conftest import CREATED, Artifacts, KeyMaterial, build_and_sign, policy, sample_doc
from dentisai_manifest.canonical import JsonValue, canonicalize, parse_strict
from dentisai_manifest.errors import ErrorCode, ExitCode, ManifestError
from dentisai_manifest.keys import fixed_test_key, key_id_of, public_b64
from dentisai_manifest.schema import MAX_MANIFEST_BYTES, Manifest
from dentisai_manifest.sigfile import parse_sig_file, sign_bytes
from dentisai_manifest.signing import sign_manifest
from dentisai_manifest.verify import Policy, TrustStore, verify_manifest


def fails(raw: bytes, sig: bytes, trust: TrustStore, pol: Policy | None = None) -> ManifestError:
    with pytest.raises(ManifestError) as ei:
        verify_manifest(raw, sig, trust, pol or policy())
    return ei.value


def signed_raw(key: KeyMaterial, raw: bytes) -> tuple[bytes, bytes]:
    """Sign arbitrary bytes (bypassing the signer's own validation)."""
    return raw, sign_bytes(key.private_key, raw)


def signed_doc(key: KeyMaterial, **over: JsonValue) -> tuple[bytes, bytes]:
    return signed_raw(key, canonicalize(sample_doc(key.key_id, **over)))


# --- round trip -------------------------------------------------------------


def test_round_trip(key: KeyMaterial, artifacts: Artifacts) -> None:
    s = build_and_sign(key, artifacts)
    m = verify_manifest(s.manifest_bytes, s.sig_bytes, key.trust(), policy())
    assert isinstance(m, Manifest)
    assert m.sequence == 5
    assert m.channel == "production"
    assert m.signer_key_id == key.key_id
    sig = json.loads(s.sig_bytes)
    assert set(sig) == {"alg", "key_id", "sig"}
    assert len(base64.b64decode(sig["sig"])) == 64


def test_sign_manifest_fills_key_id_and_rejects_mismatch(key: KeyMaterial) -> None:
    d = sample_doc(key.key_id)
    del d["signer_key_id"]
    del d["channel"]
    s = sign_manifest(key.private_key, d, allow_placeholder=False)
    assert s.manifest.signer_key_id == key.key_id
    assert s.manifest.channel == "production"
    with pytest.raises(ManifestError) as ei:
        sign_manifest(key.private_key, sample_doc("0" * 16), allow_placeholder=False)
    assert ei.value.code is ErrorCode.E_KEY_ID_MISMATCH


def test_sign_manifest_placeholder_rules(key: KeyMaterial) -> None:
    d = sample_doc(key.key_id)
    model = d["model"]
    assert isinstance(model, dict)
    model["license"] = "LicenseRef-PLACEHOLDER"
    with pytest.raises(ManifestError) as ei:
        sign_manifest(key.private_key, d, allow_placeholder=False)
    assert ei.value.code is ErrorCode.E_REFUSED
    s = sign_manifest(key.private_key, d, allow_placeholder=True)
    assert s.manifest.channel == "internal-test"


# --- tampering ----------------------------------------------------------------


def test_tampered_manifest_byte_rejected(key: KeyMaterial, artifacts: Artifacts) -> None:
    s = build_and_sign(key, artifacts)
    for i in (0, len(s.manifest_bytes) // 2, len(s.manifest_bytes) - 1):
        raw = bytearray(s.manifest_bytes)
        raw[i] ^= 0x01
        assert fails(bytes(raw), s.sig_bytes, key.trust()).code is ErrorCode.E_SIGNATURE


def test_tampered_signature_rejected(key: KeyMaterial, artifacts: Artifacts) -> None:
    s = build_and_sign(key, artifacts)
    doc = json.loads(s.sig_bytes)
    sig = bytearray(base64.b64decode(doc["sig"]))
    sig[10] ^= 0x80
    doc["sig"] = base64.b64encode(bytes(sig)).decode()
    bad = canonicalize(doc)
    assert fails(s.manifest_bytes, bad, key.trust()).code is ErrorCode.E_SIGNATURE


def test_wrong_key_rejected(key: KeyMaterial, other_key: KeyMaterial, artifacts: Artifacts) -> None:
    s = build_and_sign(key, artifacts)
    # sig claims key.key_id but only other_key is trusted -> unknown key
    assert fails(s.manifest_bytes, s.sig_bytes, other_key.trust()).code is ErrorCode.E_KEY_UNKNOWN
    # sig file forged to name the trusted key id, signature made by another key
    forged = json.loads(sign_bytes(key.private_key, s.manifest_bytes))
    forged["key_id"] = other_key.key_id
    e = fails(s.manifest_bytes, canonicalize(forged), other_key.trust())
    assert e.code is ErrorCode.E_SIGNATURE


def test_unknown_and_revoked_key(key: KeyMaterial, other_key: KeyMaterial) -> None:
    raw, sig = signed_doc(key)
    both = TrustStore.from_b64([key.public_b64, other_key.public_b64])
    verify_manifest(raw, sig, both, policy())
    revoked = TrustStore.from_b64([key.public_b64], [key.key_id])
    assert fails(raw, sig, revoked).code is ErrorCode.E_KEY_REVOKED
    assert fails(raw, sig, TrustStore({})).code is ErrorCode.E_KEY_UNKNOWN


def test_signer_key_id_must_match_sig_key(key: KeyMaterial, other_key: KeyMaterial) -> None:
    raw, sig = signed_doc(key, signer_key_id=other_key.key_id)
    assert fails(raw, sig, key.trust()).code is ErrorCode.E_KEY_ID_MISMATCH


def test_swapped_artifact_hash_rejected_by_signature(
    key: KeyMaterial, artifacts: Artifacts
) -> None:
    s = build_and_sign(key, artifacts)
    doc = json.loads(s.manifest_bytes)
    doc["model"]["sha256"], doc["model"]["labels_sha256"] = (
        doc["model"]["labels_sha256"],
        doc["model"]["sha256"],
    )
    assert fails(canonicalize(doc), s.sig_bytes, key.trust()).code is ErrorCode.E_SIGNATURE


# --- canonicalization rule --------------------------------------------------------


def test_reserialized_manifest_rule(key: KeyMaterial, artifacts: Artifacts) -> None:
    """The signature covers the exact canonical bytes.

    - Reordered / pretty-printed bytes with the ORIGINAL signature: E_SIGNATURE.
    - Same non-canonical bytes signed directly: E_NOT_CANONICAL.
    - Re-canonicalizing a semantically equal document reproduces the exact
      signed bytes, so the original signature verifies again.
    """
    s = build_and_sign(key, artifacts)
    obj = json.loads(s.manifest_bytes)
    variants = [
        json.dumps(obj, indent=2).encode(),
        json.dumps(dict(reversed(list(obj.items()))), separators=(",", ":")).encode(),
        s.manifest_bytes + b"\n",
        s.manifest_bytes.replace(b":", b": ", 1),
    ]
    for v in variants:
        assert v != s.manifest_bytes
        assert fails(v, s.sig_bytes, key.trust()).code is ErrorCode.E_SIGNATURE
        assert fails(*signed_raw(key, v), key.trust()).code is ErrorCode.E_NOT_CANONICAL
        recanon = canonicalize(parse_strict(v.decode()))
        assert recanon == s.manifest_bytes
        verify_manifest(recanon, s.sig_bytes, key.trust(), policy())


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (b'"sequence":3', b'"sequence":3,"sequence":3'),
        (b'"sequence":3', b'"sequence":3.0'),
        (b'"sequence":3', b'"sequence":3e0'),
        (b'"sequence":3', b'"sequence":NaN'),
        (b'"sequence":3', b'"sequence":Infinity'),
        (b'"sequence":3', b'"sequence":-0'),
        (b'"sequence":3', b'"sequence":9007199254740993'),
        (b'"channel":"production"', b'"channel":"production","channel":"internal-test"'),
        (b'"channel":"production"', b'"channel":"pr\\u006fduction"'),
    ],
)
def test_duplicate_keys_floats_nan_rejected(key: KeyMaterial, old: bytes, new: bytes) -> None:
    raw = canonicalize(sample_doc(key.key_id))
    assert old in raw
    e = fails(*signed_raw(key, raw.replace(old, new, 1)), key.trust())
    assert e.code is ErrorCode.E_NOT_CANONICAL


def test_oversized_inputs_rejected(key: KeyMaterial) -> None:
    raw, sig = signed_doc(key)
    big = raw[:-1] + b',"zz":"' + b"x" * MAX_MANIFEST_BYTES + b'"}'
    assert fails(*signed_raw(key, big), key.trust()).code is ErrorCode.E_TOO_LARGE
    assert fails(raw, sig + b" " * 600, key.trust()).code is ErrorCode.E_TOO_LARGE
    with pytest.raises(ManifestError) as ei:
        parse_sig_file(b" " * 600)
    assert ei.value.code is ErrorCode.E_TOO_LARGE


@pytest.mark.parametrize(
    "sig_doc",
    [
        b"",
        b"[]",
        b'{"alg":"Ed25519","key_id":"0123456789abcdef"}',
        b'{"alg":"EdDSA","key_id":"0123456789abcdef","sig":"AA=="}',
        b'{"alg":"Ed25519","key_id":"XYZ","sig":"AA=="}',
        b'{"alg":"Ed25519","key_id":"0123456789abcdef","sig":5}',
        b'{"alg":"Ed25519","key_id":"0123456789abcdef","sig":"AA=="}',
        b'{"alg":"Ed25519", "key_id":"0123456789abcdef","sig":"AA=="}',
    ],
)
def test_malformed_sig_file(key: KeyMaterial, sig_doc: bytes) -> None:
    raw, _ = signed_doc(key)
    assert fails(raw, sig_doc, key.trust()).code is ErrorCode.E_SIG_FORMAT


# --- policy ------------------------------------------------------------------------


def test_schema_version_rules(key: KeyMaterial) -> None:
    raw, sig = signed_doc(key)
    e = fails(raw, sig, key.trust(), policy(last_schema_version=2))
    assert e.code is ErrorCode.E_SCHEMA_DOWNGRADE
    verify_manifest(raw, sig, key.trust(), policy(last_schema_version=1))
    raw2, sig2 = signed_doc(key, schema_version=2)
    assert fails(raw2, sig2, key.trust()).code is ErrorCode.E_SCHEMA_VERSION_UNSUPPORTED
    raw3, sig3 = signed_doc(key, extra_field="x")
    assert fails(raw3, sig3, key.trust()).code is ErrorCode.E_SCHEMA


def test_expiry(key: KeyMaterial) -> None:
    raw, sig = signed_doc(key, expires_at="2026-10-09T00:00:00Z")
    verify_manifest(raw, sig, key.trust(), policy(now=CREATED + timedelta(hours=23)))
    for delta in (timedelta(days=1), timedelta(days=30)):
        e = fails(raw, sig, key.trust(), policy(now=CREATED + delta))
        assert e.code is ErrorCode.E_EXPIRED


def test_sequence_rollback(key: KeyMaterial) -> None:
    raw, sig = signed_doc(key, sequence=7)
    sha = hashlib.sha256(raw).hexdigest()
    verify_manifest(raw, sig, key.trust(), policy(last_sequence=6))
    verify_manifest(raw, sig, key.trust(), policy(last_sequence=7, last_manifest_sha256=sha))
    verify_manifest(raw, sig, key.trust(), policy(last_sequence=7))
    assert fails(raw, sig, key.trust(), policy(last_sequence=8)).code is ErrorCode.E_ROLLBACK
    e = fails(raw, sig, key.trust(), policy(last_sequence=7, last_manifest_sha256="0" * 64))
    assert e.code is ErrorCode.E_ROLLBACK


def test_production_channel_and_placeholder(key: KeyMaterial) -> None:
    prod = policy(production=True)
    raw, sig = signed_doc(key, channel="internal-test")
    verify_manifest(raw, sig, key.trust(), policy())
    assert fails(raw, sig, key.trust(), prod).code is ErrorCode.E_CHANNEL
    d = sample_doc(key.key_id)
    model = d["model"]
    assert isinstance(model, dict)
    model["license"] = "PLACEHOLDER"
    raw2, sig2 = signed_raw(key, canonicalize(d))
    assert fails(raw2, sig2, key.trust(), prod).code is ErrorCode.E_PLACEHOLDER
    raw3, sig3 = signed_doc(key)
    verify_manifest(raw3, sig3, key.trust(), prod)


def test_production_rejects_test_key() -> None:
    tk = fixed_test_key()
    raw = canonicalize(sample_doc(key_id_of(tk.public_key())))
    sig = sign_bytes(tk, raw)
    trust = TrustStore.from_b64([public_b64(tk.public_key())])
    verify_manifest(raw, sig, trust, policy())
    e = fails(raw, sig, trust, policy(production=True))
    assert e.code is ErrorCode.E_KEY_TEST_ONLY
    assert e.exit_code is ExitCode.SIGNATURE


def test_min_app_version(key: KeyMaterial) -> None:
    raw, sig = signed_doc(key, min_app_version="10.2.3")
    for ok in ("10.2.3", "10.10.0", "11.0.0"):
        verify_manifest(raw, sig, key.trust(), policy(app_version=ok))
    for old in ("9.99.99", "10.2.2", "1.0.0"):
        e = fails(raw, sig, key.trust(), policy(app_version=old))
        assert e.code is ErrorCode.E_APP_TOO_OLD


def test_non_object_manifest_signed(key: KeyMaterial) -> None:
    assert fails(*signed_raw(key, b"[1]"), key.trust()).code is ErrorCode.E_SCHEMA


def test_trust_store_rejects_bad_public_key() -> None:
    with pytest.raises(ValueError, match="base64"):
        TrustStore.from_b64(["not base64!"])
