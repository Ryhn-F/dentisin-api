"""Generate the cross-language test vectors in ``tools/manifest/vectors/``.

All vectors are signed with the public, deterministic TEST-ONLY keys from
:mod:`keys`. Run ``uv run python -m dentisai_manifest.vectors`` to regenerate;
``tests/test_vectors.py`` fails if the committed files drift.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Final

from cryptography.hazmat.primitives import serialization

from .canonical import JsonValue, canonicalize, parse_canonical, parse_strict
from .errors import ManifestError
from .keys import TEST_KEY_LABELS, fixed_test_key, key_id_of, public_b64, public_raw
from .schema import CHANNEL_INTERNAL_TEST, parse_timestamp
from .sigfile import sign_bytes
from .verify import Policy, TrustStore, verify_manifest

VECTOR_DIR: Final = Path(__file__).resolve().parents[2] / "vectors"
BASE: Final = "https://test-project-ref.supabase.co/storage/v1/object/public/manifests"
ED25519_L: Final = 2**252 + 27742317777372353535851937790883648493
WARNING: Final = (
    "TEST-ONLY KEY. The seed is derived from a public label. Never embed this public key "
    "or key_id in a production app; production verifiers reject these key_ids (E_KEY_TEST_ONLY)."
)


def _h(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _manifest(
    n: int,
    sequence: int,
    created_at: str,
    *,
    min_app: str = "1.0.0",
    license_: str = "Apache-2.0",
    extra: dict[str, JsonValue] | None = None,
) -> dict[str, JsonValue]:
    """Intentionally NOT in sorted key order: canonicalization must sort."""
    kid = key_id_of(fixed_test_key().public_key())
    m: dict[str, JsonValue] = {
        "schema_version": 1,
        "signer_key_id": kid,
        "sequence": sequence,
        "model": {
            "version": f"0.{n}.0",
            "url": f"{BASE}/model/0.{n}.0/model.tflite",
            "sha256": _h(f"vector-{n}-model"),
            "size_bytes": 4_812_345 + n,
            "labels_url": f"{BASE}/model/0.{n}.0/labels.json",
            "labels_sha256": _h(f"vector-{n}-labels"),
            "labels_size_bytes": 1_234 + n,
            "license": license_,
        },
        "rules": {
            "version": f"2026.10.{n}",
            "url": f"{BASE}/rules/2026.10.{n}/rules.json",
            "sha256": _h(f"vector-{n}-rules"),
            "size_bytes": 8_000 + n,
        },
        "recommender": {
            "version": f"1.{n}.0",
            "url": f"{BASE}/recommender/1.{n}.0/recommender.json",
            "sha256": _h(f"vector-{n}-rec"),
            "size_bytes": 3_000 + n,
        },
        "created_at": created_at,
        "min_app_version": min_app,
        "channel": "production",
    }
    m.update(extra or {})
    return m


def _key_block() -> dict[str, JsonValue]:
    k1, k2 = fixed_test_key(0), fixed_test_key(1)
    seed1 = k1.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    return {
        "test_only": True,
        "warning": WARNING,
        "key_derivation": "private seed = SHA-256(ASCII label); Ed25519 per RFC 8032",
        "signing_key": {
            "label": TEST_KEY_LABELS[0].decode(),
            "private_seed_hex": seed1.hex(),
            "public_key_hex": public_raw(k1.public_key()).hex(),
            "public_key_b64": public_b64(k1.public_key()),
            "key_id": key_id_of(k1.public_key()),
        },
        "other_key": {
            "label": TEST_KEY_LABELS[1].decode(),
            "public_key_b64": public_b64(k2.public_key()),
            "key_id": key_id_of(k2.public_key()),
        },
    }


def _expect(raw: bytes, sig: bytes, case: dict[str, JsonValue]) -> str:
    """Run the reference verifier for one policy case; return 'OK' or the error code."""
    trusted = case.get("trusted_public_keys_b64")
    revoked = case.get("revoked_key_ids", [])
    assert isinstance(trusted, list)
    assert isinstance(revoked, list)
    now = case["now"]
    assert isinstance(now, str)

    def opt_int(name: str) -> int | None:
        v = case.get(name)
        assert v is None or isinstance(v, int)
        return v

    last_sha = case.get("last_manifest_sha256")
    app = case.get("app_version")
    assert last_sha is None or isinstance(last_sha, str)
    assert app is None or isinstance(app, str)
    policy = Policy(
        now=parse_timestamp(now),
        production=bool(case.get("production", False)),
        last_sequence=opt_int("last_sequence"),
        last_manifest_sha256=last_sha,
        last_schema_version=opt_int("last_schema_version"),
        app_version=app,
    )
    try:
        verify_manifest(
            raw,
            sig,
            TrustStore.from_b64([str(t) for t in trusted], [str(r) for r in revoked]),
            policy,
        )
    except ManifestError as exc:
        return str(exc.code)
    return "OK"


def _cases(raw: bytes, sig: bytes, doc: dict[str, JsonValue]) -> list[JsonValue]:
    keys = _key_block()
    sk, ok_ = keys["signing_key"], keys["other_key"]
    assert isinstance(sk, dict)
    assert isinstance(ok_, dict)
    pub, other = sk["public_key_b64"], ok_["public_key_b64"]
    created = doc["created_at"]
    seq = doc["sequence"]
    assert isinstance(created, str)
    assert isinstance(seq, int)
    after = (parse_timestamp(created) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    sha = hashlib.sha256(raw).hexdigest()
    min_app = doc["min_app_version"]
    assert isinstance(min_app, str)
    major, minor, _patch = (int(x) for x in min_app.split("."))
    older = f"{major - 1}.99.99" if major else f"0.{minor - 1}.99"
    newer = f"{major}.{minor + 8}.0"
    base: dict[str, JsonValue] = {"trusted_public_keys_b64": [pub, other], "now": after}
    variants: list[tuple[str, dict[str, JsonValue]]] = [
        ("default policy", {}),
        ("trusted keys list does not contain the signer", {"trusted_public_keys_b64": [other]}),
        ("signer key revoked", {"revoked_key_ids": [sk["key_id"]]}),
        ("production policy rejects TEST key", {"production": True}),
        ("rollback: last_sequence = sequence + 1", {"last_sequence": seq + 1}),
        (
            "same sequence, same bytes (idempotent re-check)",
            {"last_sequence": seq, "last_manifest_sha256": sha},
        ),
        (
            "same sequence, different bytes (equivocation)",
            {"last_sequence": seq, "last_manifest_sha256": "0" * 64},
        ),
        ("older sequence seen before", {"last_sequence": seq - 1}),
        ("schema downgrade: last_schema_version = 2", {"last_schema_version": 2}),
        ("app exactly at min_app_version", {"app_version": doc["min_app_version"]}),
        ("app older than min_app_version (numeric, not string, compare)", {"app_version": older}),
        ("app newer by minor (numeric, not string, compare)", {"app_version": newer}),
    ]
    if "expires_at" in doc:
        exp = doc["expires_at"]
        assert isinstance(exp, str)
        before = (parse_timestamp(exp) - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        variants += [
            ("one second before expires_at", {"now": before}),
            ("now == expires_at is expired", {"now": exp}),
        ]
    out: list[JsonValue] = []
    for name, override in variants:
        case = {**base, **override}
        case_json: dict[str, JsonValue] = {"name": name, **case}
        case_json["expect"] = _expect(raw, sig, case)
        out.append(case_json)
    return out


def valid_vectors() -> dict[str, dict[str, JsonValue]]:
    key = fixed_test_key()
    docs = {
        "manifest_v1_valid_01_minimal.json": (
            "Minimal production manifest, no expires_at.",
            _manifest(1, 1, "2026-10-08T00:00:00Z"),
        ),
        "manifest_v1_valid_02_internal_test_expiring.json": (
            "internal-test channel with expires_at and a PLACEHOLDER license "
            "(accepted only outside production).",
            _manifest(
                2,
                7,
                "2026-10-08T12:30:45Z",
                license_="LicenseRef-PLACEHOLDER",
                extra={"channel": CHANNEL_INTERNAL_TEST, "expires_at": "2026-11-08T12:30:45Z"},
            ),
        ),
        "manifest_v1_valid_03_escapes_and_semver.json": (
            'License string exercises the only two escapes (\\" and \\\\); min_app_version 10.2.3 '
            "must compare numerically (0.9.99 < 10.2.3); large sequence.",
            _manifest(
                3,
                9_007_199_254_740_991,
                "2026-12-31T23:59:59Z",
                min_app="10.2.3",
                license_='LicenseRef-"DentisAI"\\internal AND CC-BY-4.0',
                extra={"expires_at": "2027-06-30T00:00:00Z"},
            ),
        ),
    }
    out: dict[str, dict[str, JsonValue]] = {}
    for fname, (desc, doc) in docs.items():
        raw = canonicalize(doc)
        sig = sign_bytes(key, raw)
        sig_doc = json.loads(sig)
        vec: dict[str, JsonValue] = {
            **_key_block(),
            "description": desc,
            "manifest_object_unsorted": doc,
            "manifest_json": raw.decode("ascii"),
            "canonical_hex": raw.hex(),
            "canonical_sha256": hashlib.sha256(raw).hexdigest(),
            "manifest_sig": sig.decode("ascii"),
            "signature_hex": base64.b64decode(sig_doc["sig"]).hex(),
            "cases": _cases(raw, sig, doc),
        }
        out[fname] = vec
    return out


def _resign(doc_bytes: bytes) -> bytes:
    return sign_bytes(fixed_test_key(), doc_bytes)


def invalid_vectors() -> dict[str, JsonValue]:
    key = fixed_test_key()
    good_doc = _manifest(4, 10, "2026-10-08T00:00:00Z")
    raw = canonicalize(good_doc)
    sig = sign_bytes(key, raw)
    sig_doc = json.loads(sig)
    sig_raw = base64.b64decode(sig_doc["sig"])

    def sig_with(signature: bytes, **over: str) -> bytes:
        d: dict[str, JsonValue] = {**sig_doc, "sig": base64.b64encode(signature).decode()}
        d.update(over)
        return canonicalize(d)

    s_int = int.from_bytes(sig_raw[32:], "little")
    noncanon_s = sig_raw[:32] + (s_int + ED25519_L).to_bytes(32, "little")
    flipped = bytearray(raw)
    flipped[raw.index(b'"sequence":10') + len(b'"sequence":1')] = ord("1")  # 10 -> 11
    flipped_sig = bytearray(sig_raw)
    flipped_sig[0] ^= 0x01

    pretty = json.dumps(json.loads(raw), indent=2).encode()
    reordered = json.dumps(good_doc, separators=(",", ":")).encode()
    dup = raw[:-1] + b',"sequence":10}'
    as_float = raw.replace(b'"sequence":10', b'"sequence":10.0')
    as_exp = raw.replace(b'"sequence":10', b'"sequence":1e1')
    unknown_doc = {**good_doc, "zz_extra": "x"}
    v2_doc = {**good_doc, "schema_version": 2}
    other_kid_doc = {**good_doc, "signer_key_id": key_id_of(fixed_test_key(1).public_key())}

    entries: list[tuple[str, bytes, bytes]] = [
        ("one manifest byte changed after signing (sequence 10 -> 11)", bytes(flipped), sig),
        ("one signature bit flipped", raw, sig_with(bytes(flipped_sig))),
        (
            "non-canonical signature scalar S+L (must reject; use strict RFC 8032 / zip215:false)",
            raw,
            sig_with(noncanon_s),
        ),
        ("signature by the other (untrusted) test key", raw, sign_bytes(fixed_test_key(1), raw)),
        ("manifest.sig alg is not Ed25519", raw, sig_with(sig_raw, alg="EdDSA")),
        (
            "manifest.sig sig is not canonical base64 (padding stripped)",
            raw,
            canonicalize({**sig_doc, "sig": sig_doc["sig"].rstrip("=")}),
        ),
        ("manifest.sig has a trailing newline", raw, sig + b"\n"),
        ("pretty-printed manifest, validly signed over the pretty bytes", pretty, _resign(pretty)),
        ("unsorted keys, validly signed over those bytes", reordered, _resign(reordered)),
        ("trailing newline, validly signed", raw + b"\n", _resign(raw + b"\n")),
        ("duplicate key, validly signed", dup, _resign(dup)),
        ("float 10.0, validly signed", as_float, _resign(as_float)),
        ("exponent 1e1, validly signed", as_exp, _resign(as_exp)),
        (
            "integer 2^53 (unsafe), validly signed",
            raw.replace(b'"sequence":10', b'"sequence":9007199254740992'),
            _resign(raw.replace(b'"sequence":10', b'"sequence":9007199254740992')),
        ),
        (
            "unknown top-level field, validly signed",
            canonicalize(unknown_doc),
            _resign(canonicalize(unknown_doc)),
        ),
        (
            "schema_version 2 (unsupported by v1 apps), validly signed",
            canonicalize(v2_doc),
            _resign(canonicalize(v2_doc)),
        ),
        (
            "signer_key_id names a different key than manifest.sig",
            canonicalize(other_kid_doc),
            _resign(canonicalize(other_kid_doc)),
        ),
    ]
    keys = _key_block()
    sk = keys["signing_key"]
    assert isinstance(sk, dict)
    case: dict[str, JsonValue] = {
        "trusted_public_keys_b64": [sk["public_key_b64"]],
        "now": "2026-10-09T00:00:00Z",
    }
    out: list[JsonValue] = []
    for name, m, s in entries:
        out.append(
            {
                "name": name,
                "manifest_hex": m.hex(),
                "manifest_sig_hex": s.hex(),
                "expect": _expect(m, s, case),
            }
        )
    return {
        **keys,
        "description": "Each entry must be rejected with exactly the given code under `policy`.",
        "policy": case,
        "entries": out,
    }


CANONICALIZE_INPUTS: Final[list[tuple[str, str]]] = [
    ("sorts keys at every depth", '{"b":1,"a":{"d":[3,{"z":true,"y":null}],"c":"x"}}'),
    ("drops insignificant whitespace", ' {\n  "a" : [ 1 , 2 ] ,\t"b" : false }\n'),
    ("escapes only quote and backslash", '{"s":"say \\"hi\\" \\\\ bye / ok"}'),
    ("\\u escapes of printable ASCII are emitted literally", '{"s":"\\u0041\\/"}'),
    ("negative and boundary integers", '{"a":-9007199254740991,"b":0,"c":9007199254740991}'),
    ("empty containers", '{"a":{},"b":[],"c":""}'),
]

PARSE_CANONICAL_INPUTS: Final[list[tuple[str, bytes]]] = [
    ("canonical object", b'{"a":1,"b":[true,false,null],"c":"x"}'),
    ("unsorted keys", b'{"b":1,"a":2}'),
    ("whitespace", b'{"a": 1}'),
    ("trailing newline", b'{"a":1}\n'),
    ("UTF-8 BOM", b'\xef\xbb\xbf{"a":1}'),
    ("non-ASCII UTF-8 in string", '{"a":"é"}'.encode()),
    ("duplicate key", b'{"a":1,"a":1}'),
    ("float 0.5 (re-serializes identically in JS: must be rejected explicitly)", b'{"a":0.5}'),
    ("float 1.0", b'{"a":1.0}'),
    ("exponent", b'{"a":1e2}'),
    ("negative zero", b'{"a":-0}'),
    (
        "2^53 (re-serializes identically in JS: must be rejected explicitly)",
        b'{"a":9007199254740992}',
    ),
    ("NaN", b'{"a":NaN}'),
    ("lone surrogate escape (must be rejected explicitly)", b'{"a":"\\ud800"}'),
    ("control char escape", b'{"a":"\\n"}'),
    ("unnecessary \\u escape", b'{"a":"\\u0041"}'),
    ("escaped slash", b'{"a":"\\/"}'),
    ("uppercase key", b'{"A":1}'),
    ("__proto__ key", b'{"__proto__":1}'),
    ("depth 16 ok", b"[" * 15 + b"{}" + b"]" * 15),
    ("depth 17 rejected", b"[" * 16 + b"{}" + b"]" * 16),
    ("top-level string is canonical JSON (manifest schema then rejects it)", b'"x"'),
]


def canonicalization_vectors() -> dict[str, JsonValue]:
    canon: list[JsonValue] = []
    for name, text in CANONICALIZE_INPUTS:
        canon.append(
            {"name": name, "input": text, "canonical": canonicalize(parse_strict(text)).decode()}
        )
    parsed: list[JsonValue] = []
    for name, raw in PARSE_CANONICAL_INPUTS:
        try:
            parse_canonical(raw)
            expect = "OK"
        except ManifestError as exc:
            expect = str(exc.code)
        parsed.append({"name": name, "bytes_hex": raw.hex(), "expect": expect})
    return {
        "description": "DCJ-1 canonicalization (SPEC.md section 3). `canonicalize`: "
        "strict-parse input then serialize. `parse_canonical`: raw wire bytes must be "
        "accepted (OK) or rejected (E_NOT_CANONICAL).",
        "canonicalize": canon,
        "parse_canonical": parsed,
    }


def render_all() -> dict[str, str]:
    files: dict[str, JsonValue] = {
        **valid_vectors(),
        "manifest_v1_invalid.json": invalid_vectors(),
        "canonicalization.json": canonicalization_vectors(),
    }
    return {
        name: json.dumps(doc, indent=2, ensure_ascii=True) + "\n" for name, doc in files.items()
    }


def write_all(
    target: Path = VECTOR_DIR, writer: Callable[[Path, str], None] | None = None
) -> list[Path]:
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, text in render_all().items():
        p = target / name
        (writer or (lambda path, t: path.write_text(t, encoding="utf-8", newline="\n")))(p, text)
        written.append(p)
    return written


if __name__ == "__main__":  # pragma: no cover
    for path in write_all():
        print(path, file=sys.stdout)
