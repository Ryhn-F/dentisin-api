"""Signed-manifest VERIFICATION (never signing) for model/rules registration.

Independent re-implementation of the verifier contract of the manifest
tooling (backend/tools/manifest, ``dentisai_manifest.verify``; DCJ-1 canonical
JSON + Ed25519 + closed schema v1). It is re-implemented instead of imported
because that package is not installable (``tool.uv.package = false``) and is
still being finished; parity is enforced by tests/test_manifest_vectors.py,
which runs every published vector in backend/tools/manifest/vectors/*.json.

The server only ever holds PUBLIC keys (``MANIFEST_PUBLIC_KEY_B64``).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, NoReturn

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

JsonValue = bool | int | str | list["JsonValue"] | dict[str, "JsonValue"] | None

MAX_SAFE_INTEGER: Final = 2**53 - 1
MAX_DEPTH: Final = 16
MAX_MANIFEST_BYTES: Final = 16 * 1024
MAX_SIG_BYTES: Final = 512
SUPPORTED_SCHEMA_VERSIONS: Final = frozenset({1})
CHANNEL_PRODUCTION: Final = "production"
CHANNELS: Final = frozenset({CHANNEL_PRODUCTION, "internal-test"})
PLACEHOLDER_MARKER: Final = "PLACEHOLDER"
MIB: Final = 1024 * 1024

KEY_RE: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")
INT_RE: Final = re.compile(r"-?(0|[1-9][0-9]*)")
SHA256_RE: Final = re.compile(r"[0-9a-f]{64}")
KEY_ID_RE: Final = re.compile(r"[0-9a-f]{16}")
VERSION_RE: Final = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}")
APP_VERSION_RE: Final = re.compile(r"(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})")
TIMESTAMP_RE: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
URL_RE: Final = re.compile(r"https://[a-z0-9.-]{1,253}(:[0-9]{1,5})?/[A-Za-z0-9._~%/+-]{1,1900}")
LICENSE_MAX_LEN: Final = 256

# Public, deterministic TEST-ONLY keys of the vectors (seed = SHA-256(label)).
# Production policy rejects them (E_KEY_TEST_ONLY).
TEST_KEY_IDS: Final = frozenset({"965ba6b27d5ca7a7", "e12ef5f599e59801"})

_FILE_FIELDS: Final = ("url", "sha256", "size_bytes")
# (component, prefix, max size, required)
_ARTIFACTS: Final = (
    ("model", "", 50 * MIB, True),
    ("model", "labels_", 1 * MIB, True),
    ("rules", "", 2 * MIB, True),
    ("recommender", "", 2 * MIB, True),
    # products.json: present in the published vectors; accepted as an
    # all-or-nothing triple so this verifier works with both revisions of
    # the schema while the tooling is being finished. [open question]
    ("recommender", "products_", 2 * MIB, False),
)
_COMPONENT_REQUIRED: Final[dict[str, frozenset[str]]] = {
    "model": frozenset(
        {"version", "license", *_FILE_FIELDS, *(f"labels_{f}" for f in _FILE_FIELDS)}
    ),
    "rules": frozenset({"version", *_FILE_FIELDS}),
    "recommender": frozenset({"version", *_FILE_FIELDS}),
}
_COMPONENT_OPTIONAL: Final[dict[str, frozenset[str]]] = {
    "model": frozenset(),
    "rules": frozenset(),
    "recommender": frozenset(f"products_{f}" for f in _FILE_FIELDS),
}
_REQUIRED_TOP: Final = frozenset(
    {
        "schema_version",
        "sequence",
        "created_at",
        "min_app_version",
        "signer_key_id",
        "channel",
        "model",
        "rules",
        "recommender",
    }
)
_OPTIONAL_TOP: Final = frozenset({"expires_at"})

# Error codes (string values are the shared contract with the tooling/app).
E_TOO_LARGE: Final = "E_TOO_LARGE"
E_SIG_FORMAT: Final = "E_SIG_FORMAT"
E_KEY_UNKNOWN: Final = "E_KEY_UNKNOWN"
E_KEY_REVOKED: Final = "E_KEY_REVOKED"
E_KEY_TEST_ONLY: Final = "E_KEY_TEST_ONLY"
E_SIGNATURE: Final = "E_SIGNATURE"
E_NOT_CANONICAL: Final = "E_NOT_CANONICAL"
E_SCHEMA: Final = "E_SCHEMA"
E_SCHEMA_VERSION_UNSUPPORTED: Final = "E_SCHEMA_VERSION_UNSUPPORTED"
E_KEY_ID_MISMATCH: Final = "E_KEY_ID_MISMATCH"
E_SCHEMA_DOWNGRADE: Final = "E_SCHEMA_DOWNGRADE"
E_EXPIRED: Final = "E_EXPIRED"
E_ROLLBACK: Final = "E_ROLLBACK"
E_APP_TOO_OLD: Final = "E_APP_TOO_OLD"
E_CHANNEL: Final = "E_CHANNEL"
E_PLACEHOLDER: Final = "E_PLACEHOLDER"


class ManifestError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _reject(message: str) -> NoReturn:
    raise ManifestError(E_NOT_CANONICAL, message)


# ---------------------------------------------------------------------------
# DCJ-1 canonical JSON
# ---------------------------------------------------------------------------
def is_printable_ascii(data: bytes | str) -> bool:
    if isinstance(data, str):
        return all(0x20 <= ord(c) <= 0x7E for c in data)
    return all(0x20 <= b <= 0x7E for b in data)


def _encode(value: object, depth: int, out: list[str]) -> None:
    if depth > MAX_DEPTH:
        _reject("nesting too deep")
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, int):
        if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            _reject("integer outside the safe range")
        out.append(str(value))
    elif isinstance(value, str):
        if not is_printable_ascii(value):
            _reject("string outside printable ASCII")
        out.append('"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"')
    elif isinstance(value, list):
        out.append("[")
        for i, item in enumerate(value):
            if i:
                out.append(",")
            _encode(item, depth + 1, out)
        out.append("]")
    elif isinstance(value, dict):
        for k in value:
            if not isinstance(k, str) or not KEY_RE.fullmatch(k):
                _reject("object key not allowed")
        out.append("{")
        for i, k in enumerate(sorted(value)):
            if i:
                out.append(",")
            out.append('"' + k + '":')
            _encode(value[k], depth + 1, out)
        out.append("}")
    else:
        _reject("unsupported JSON value type")


def canonicalize(value: JsonValue) -> bytes:
    out: list[str] = []
    _encode(value, 1, out)
    return "".join(out).encode("ascii")


def _no_duplicates(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for k, v in pairs:
        if k in result:
            _reject("duplicate object key")
        result[k] = v
    return result


def _parse_int(token: str) -> int:
    if not INT_RE.fullmatch(token) or token == "-0":  # noqa: S105
        _reject("integer not in shortest form")
    value = int(token)
    if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
        _reject("integer outside the safe range")
    return value


def _parse_float(token: str) -> NoReturn:
    _reject("floats/exponents are forbidden")


def _parse_constant(token: str) -> NoReturn:
    _reject("NaN/Infinity are not JSON")


def parse_strict(text: str) -> JsonValue:
    try:
        value: JsonValue = json.loads(
            text,
            object_pairs_hook=_no_duplicates,
            parse_int=_parse_int,
            parse_float=_parse_float,
            parse_constant=_parse_constant,
        )
    except ManifestError:
        raise
    except RecursionError:
        _reject("nesting too deep")
    except ValueError:
        _reject("invalid JSON")
    canonicalize(value)
    return value


def parse_canonical(raw: bytes) -> JsonValue:
    if not is_printable_ascii(raw):
        _reject("bytes outside printable ASCII")
    value = parse_strict(raw.decode("ascii"))
    if canonicalize(value) != raw:
        _reject("bytes are not in canonical form")
    return value


# ---------------------------------------------------------------------------
# Keys and manifest.sig
# ---------------------------------------------------------------------------
def b64decode_canonical(text: str, expected_len: int) -> bytes:
    try:
        raw = base64.b64decode(text.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError, ValueError):
        raise ValueError("not valid base64") from None
    if base64.b64encode(raw).decode("ascii") != text or len(raw) != expected_len:
        raise ValueError("not canonical base64 of the expected length")
    return raw


def key_id_of(raw_public_key: bytes) -> str:
    return hashlib.sha256(raw_public_key).digest()[:8].hex()


@dataclass(frozen=True)
class TrustStore:
    keys: dict[str, Ed25519PublicKey]
    revoked: frozenset[str] = frozenset()

    @classmethod
    def from_b64(cls, public_keys: Iterable[str], revoked: Iterable[str] = ()) -> TrustStore:
        keys: dict[str, Ed25519PublicKey] = {}
        for text in public_keys:
            raw = b64decode_canonical(text.strip(), 32)
            keys[key_id_of(raw)] = Ed25519PublicKey.from_public_bytes(raw)
        return cls(keys, frozenset(revoked))


@dataclass(frozen=True)
class SignatureFile:
    key_id: str
    signature: bytes


def parse_sig_file(raw: bytes) -> SignatureFile:
    if len(raw) > MAX_SIG_BYTES:
        raise ManifestError(E_TOO_LARGE, "manifest.sig too large")
    try:
        doc = parse_canonical(raw)
    except ManifestError:
        raise ManifestError(E_SIG_FORMAT, "manifest.sig is not canonical JSON") from None
    if not isinstance(doc, dict) or set(doc) != {"alg", "key_id", "sig"}:
        raise ManifestError(E_SIG_FORMAT, "manifest.sig must have exactly alg, key_id, sig")
    alg, key_id, sig = doc["alg"], doc["key_id"], doc["sig"]
    if alg != "Ed25519":
        raise ManifestError(E_SIG_FORMAT, "unsupported alg")
    if not isinstance(key_id, str) or not KEY_ID_RE.fullmatch(key_id):
        raise ManifestError(E_SIG_FORMAT, "key_id must be 16 lowercase hex characters")
    if not isinstance(sig, str):
        raise ManifestError(E_SIG_FORMAT, "sig must be a string")
    try:
        signature = b64decode_canonical(sig, 64)
    except ValueError:
        raise ManifestError(E_SIG_FORMAT, "sig is not canonical base64 of 64 bytes") from None
    return SignatureFile(key_id, signature)


# ---------------------------------------------------------------------------
# Schema v1 (closed)
# ---------------------------------------------------------------------------
def _bad(path: str, message: str) -> NoReturn:
    raise ManifestError(E_SCHEMA, f"{path}: {message}")


def parse_timestamp(value: str, path: str) -> datetime:
    if not TIMESTAMP_RE.fullmatch(value):
        _bad(path, "must match YYYY-MM-DDTHH:MM:SSZ")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        _bad(path, "is not a valid date/time")


def parse_app_version(value: str) -> tuple[int, int, int]:
    if not APP_VERSION_RE.fullmatch(value):
        raise ValueError("not MAJOR.MINOR.PATCH")
    a, b, c = value.split(".")
    return int(a), int(b), int(c)


def _str(obj: dict[str, JsonValue], key: str, path: str) -> str:
    v = obj[key]
    if not isinstance(v, str):
        _bad(f"{path}.{key}", "must be a string")
    return v


def _int(obj: dict[str, JsonValue], key: str, path: str, lo: int, hi: int) -> int:
    v = obj[key]
    if isinstance(v, bool) or not isinstance(v, int):
        _bad(f"{path}.{key}", "must be an integer")
    if not lo <= v <= hi:
        _bad(f"{path}.{key}", "out of range")
    return v


def _check_keys(
    obj: dict[str, JsonValue], required: frozenset[str], optional: frozenset[str], path: str
) -> None:
    if required - obj.keys():
        _bad(path, "missing required field(s)")
    if obj.keys() - required - optional:
        _bad(path, "unknown field(s) (schema is closed)")


def _check_url(url: str, path: str) -> None:
    if not URL_RE.fullmatch(url):
        _bad(path, "must be an absolute https URL without query/fragment/userinfo")
    if any(s in ("", ".", "..") for s in url[len("https://") :].split("/")[1:]):
        _bad(path, "must not contain empty, '.' or '..' path segments")


def peek_schema_version(obj: JsonValue) -> int:
    if not isinstance(obj, dict):
        _bad("$", "manifest must be a JSON object")
    if "schema_version" not in obj:
        _bad("$", "missing schema_version")
    return _int(obj, "schema_version", "$", 1, MAX_SAFE_INTEGER)


@dataclass(frozen=True)
class VerifiedManifest:
    raw: dict[str, JsonValue]
    manifest_sha256: str
    schema_version: int
    sequence: int
    created_at: datetime
    expires_at: datetime | None
    min_app_version: str
    signer_key_id: str
    channel: str

    def component(self, name: str) -> dict[str, JsonValue]:
        c = self.raw[name]
        if not isinstance(c, dict):  # pragma: no cover - guaranteed by validate_manifest
            _bad(f"$.{name}", "must be an object")
        return c


def validate_manifest(obj: JsonValue, manifest_sha256: str) -> VerifiedManifest:
    version = peek_schema_version(obj)
    if not isinstance(obj, dict):  # pragma: no cover - peek_schema_version checked it
        _bad("$", "manifest must be a JSON object")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ManifestError(E_SCHEMA_VERSION_UNSUPPORTED, "schema_version not supported")
    _check_keys(obj, _REQUIRED_TOP, _OPTIONAL_TOP, "$")
    sequence = _int(obj, "sequence", "$", 1, MAX_SAFE_INTEGER)
    created_at = parse_timestamp(_str(obj, "created_at", "$"), "$.created_at")
    expires_at: datetime | None = None
    if "expires_at" in obj:
        expires_at = parse_timestamp(_str(obj, "expires_at", "$"), "$.expires_at")
        if expires_at <= created_at:
            _bad("$.expires_at", "must be later than created_at")
    min_app = _str(obj, "min_app_version", "$")
    if not APP_VERSION_RE.fullmatch(min_app):
        _bad("$.min_app_version", "must be MAJOR.MINOR.PATCH")
    key_id = _str(obj, "signer_key_id", "$")
    if not KEY_ID_RE.fullmatch(key_id):
        _bad("$.signer_key_id", "must be 16 lowercase hex characters")
    channel = _str(obj, "channel", "$")
    if channel not in CHANNELS:
        _bad("$.channel", "unknown channel")
    for comp, required in _COMPONENT_REQUIRED.items():
        c = obj[comp]
        path = f"$.{comp}"
        if not isinstance(c, dict):
            _bad(path, "must be an object")
        _check_keys(c, required, _COMPONENT_OPTIONAL[comp], path)
        if not VERSION_RE.fullmatch(_str(c, "version", path)):
            _bad(f"{path}.version", "invalid version")
        if "license" in required:
            lic = _str(c, "license", path)
            if not 1 <= len(lic) <= LICENSE_MAX_LEN or not is_printable_ascii(lic):
                _bad(f"{path}.license", "invalid license string")
    for comp, prefix, max_size, required_triple in _ARTIFACTS:
        c = obj[comp]
        if not isinstance(c, dict):  # pragma: no cover - checked in the loop above
            _bad(f"$.{comp}", "must be an object")
        path = f"$.{comp}"
        present = [prefix + f in c for f in _FILE_FIELDS]
        if not required_triple:
            if not any(present):
                continue
            if not all(present):
                _bad(path, f"{prefix}url/sha256/size_bytes must appear together")
        _check_url(_str(c, prefix + "url", path), f"{path}.{prefix}url")
        if not SHA256_RE.fullmatch(_str(c, prefix + "sha256", path)):
            _bad(f"{path}.{prefix}sha256", "must be 64 lowercase hex characters")
        _int(c, prefix + "size_bytes", path, 1, max_size)
    return VerifiedManifest(
        raw=obj,
        manifest_sha256=manifest_sha256,
        schema_version=version,
        sequence=sequence,
        created_at=created_at,
        expires_at=expires_at,
        min_app_version=min_app,
        signer_key_id=key_id,
        channel=channel,
    )


def contains_placeholder(value: JsonValue) -> bool:
    if isinstance(value, str):
        return PLACEHOLDER_MARKER in value
    if isinstance(value, list):
        return any(contains_placeholder(v) for v in value)
    if isinstance(value, dict):
        return any(contains_placeholder(v) for v in value.values())
    return False


# ---------------------------------------------------------------------------
# Verification (same steps and order as the reference verifier)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Policy:
    now: datetime
    production: bool = False
    last_sequence: int | None = None
    last_manifest_sha256: str | None = None
    last_schema_version: int | None = None
    app_version: str | None = None


def verify_manifest(
    raw_manifest: bytes, raw_sig: bytes, trust: TrustStore, policy: Policy
) -> VerifiedManifest:
    # 1. size caps before parsing
    if len(raw_manifest) > MAX_MANIFEST_BYTES:
        raise ManifestError(E_TOO_LARGE, "manifest.json too large")
    if len(raw_sig) > MAX_SIG_BYTES:
        raise ManifestError(E_TOO_LARGE, "manifest.sig too large")
    # 2. signature file
    sig = parse_sig_file(raw_sig)
    # 3. key selection (revocation wins over trust)
    if sig.key_id in trust.revoked:
        raise ManifestError(E_KEY_REVOKED, "signing key is revoked")
    if policy.production and sig.key_id in TEST_KEY_IDS:
        raise ManifestError(E_KEY_TEST_ONLY, "signing key is the public TEST key")
    public_key = trust.keys.get(sig.key_id)
    if public_key is None:
        raise ManifestError(E_KEY_UNKNOWN, "signing key is not trusted")
    # 4. Ed25519 over the exact received bytes (verify before parse).
    #    OpenSSL rejects non-canonical S (S >= L), as the vectors require.
    try:
        public_key.verify(sig.signature, raw_manifest)
    except InvalidSignature:
        raise ManifestError(E_SIGNATURE, "Ed25519 signature does not verify") from None
    # 5. exact canonical bytes
    doc = parse_canonical(raw_manifest)
    # 6. schema_version downgrade
    version = peek_schema_version(doc)
    if policy.last_schema_version is not None and version < policy.last_schema_version:
        raise ManifestError(E_SCHEMA_DOWNGRADE, "schema_version downgrade")
    # 7. closed schema
    manifest_sha256 = hashlib.sha256(raw_manifest).hexdigest()
    manifest = validate_manifest(doc, manifest_sha256)
    # 8. signed key id must equal the verifying key
    if manifest.signer_key_id != sig.key_id:
        raise ManifestError(E_KEY_ID_MISMATCH, "signer_key_id differs from manifest.sig key_id")
    # 9. channel / placeholders in production
    if policy.production:
        if manifest.channel != CHANNEL_PRODUCTION:
            raise ManifestError(E_CHANNEL, "non-production channel rejected in production")
        if contains_placeholder(doc):
            raise ManifestError(E_PLACEHOLDER, "manifest contains PLACEHOLDER")
    # 10. expiry
    if manifest.expires_at is not None and policy.now >= manifest.expires_at:
        raise ManifestError(E_EXPIRED, "manifest expired")
    # 11. rollback protection
    if policy.last_sequence is not None:
        if manifest.sequence < policy.last_sequence:
            raise ManifestError(E_ROLLBACK, "sequence lower than the last registered one")
        if (
            manifest.sequence == policy.last_sequence
            and policy.last_manifest_sha256 is not None
            and manifest_sha256 != policy.last_manifest_sha256
        ):
            raise ManifestError(E_ROLLBACK, "same sequence but different manifest bytes")
    # 12. minimum app version
    if policy.app_version is not None and parse_app_version(policy.app_version) < parse_app_version(
        manifest.min_app_version
    ):
        raise ManifestError(E_APP_TOO_OLD, "app older than min_app_version")
    return manifest
