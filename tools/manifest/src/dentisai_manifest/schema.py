"""Manifest schema v1 (closed schema). See SPEC.md section 2."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, NoReturn

from .canonical import MAX_SAFE_INTEGER, JsonValue, is_printable_ascii
from .errors import ErrorCode, ManifestError

SCHEMA_VERSION: Final = 1
SUPPORTED_SCHEMA_VERSIONS: Final = frozenset({1})

CHANNEL_PRODUCTION: Final = "production"
CHANNEL_INTERNAL_TEST: Final = "internal-test"
CHANNELS: Final = frozenset({CHANNEL_PRODUCTION, CHANNEL_INTERNAL_TEST})

PLACEHOLDER_MARKER: Final = "PLACEHOLDER"

MAX_MANIFEST_BYTES: Final = 16 * 1024
MAX_SIG_BYTES: Final = 512

MIB: Final = 1024 * 1024

SHA256_RE: Final = re.compile(r"[0-9a-f]{64}")
KEY_ID_RE: Final = re.compile(r"[0-9a-f]{16}")
VERSION_RE: Final = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}")
APP_VERSION_RE: Final = re.compile(r"(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})")
TIMESTAMP_RE: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
URL_RE: Final = re.compile(r"https://[a-z0-9.-]{1,253}(:[0-9]{1,5})?/[A-Za-z0-9._~%/+-]{1,1900}")
LICENSE_MAX_LEN: Final = 256


@dataclass(frozen=True)
class ArtifactSpec:
    """One downloadable file described by the manifest."""

    name: str  # stable id, e.g. "model.labels"
    component: str  # "model" | "rules" | "recommender"
    prefix: str  # "" or "labels_"
    max_size: int  # hard cap in bytes enforced before/while downloading


# Order matters: it is the order artifacts are reported/downloaded.
ARTIFACT_SPECS: Final[tuple[ArtifactSpec, ...]] = (
    ArtifactSpec("model", "model", "", 50 * MIB),
    ArtifactSpec("model.labels", "model", "labels_", 1 * MIB),
    ArtifactSpec("rules", "rules", "", 2 * MIB),
    ArtifactSpec("recommender", "recommender", "", 2 * MIB),
)

_FILE_FIELDS: Final = ("url", "sha256", "size_bytes")

COMPONENT_KEYS: Final[dict[str, frozenset[str]]] = {
    "model": frozenset(
        {"version", "license", *_FILE_FIELDS, *(f"labels_{f}" for f in _FILE_FIELDS)}
    ),
    "rules": frozenset({"version", *_FILE_FIELDS}),
    "recommender": frozenset({"version", *_FILE_FIELDS}),
}

REQUIRED_TOP: Final = frozenset(
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
OPTIONAL_TOP: Final = frozenset({"expires_at"})


@dataclass(frozen=True)
class ArtifactRef:
    name: str
    url: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class Manifest:
    raw: dict[str, JsonValue]
    schema_version: int
    sequence: int
    created_at: datetime
    expires_at: datetime | None
    min_app_version: str
    signer_key_id: str
    channel: str

    def artifacts(self) -> list[ArtifactRef]:
        refs: list[ArtifactRef] = []
        for spec in ARTIFACT_SPECS:
            comp = self.raw[spec.component]
            assert isinstance(comp, dict)  # guaranteed by validate_manifest
            url, sha, size = (comp[spec.prefix + f] for f in _FILE_FIELDS)
            assert isinstance(url, str)
            assert isinstance(sha, str)
            assert isinstance(size, int)
            refs.append(ArtifactRef(spec.name, url, sha, size))
        return refs

    def summary(self) -> dict[str, JsonValue]:
        out: dict[str, JsonValue] = {
            "schema_version": self.schema_version,
            "sequence": self.sequence,
            "channel": self.channel,
            "signer_key_id": self.signer_key_id,
            "created_at": format_timestamp(self.created_at),
            "expires_at": format_timestamp(self.expires_at) if self.expires_at else None,
            "min_app_version": self.min_app_version,
        }
        for comp in COMPONENT_KEYS:
            c = self.raw[comp]
            assert isinstance(c, dict)
            out[f"{comp}_version"] = c["version"]
        return out


def _bad(path: str, message: str) -> NoReturn:
    raise ManifestError(ErrorCode.E_SCHEMA, f"{path}: {message}")


def parse_timestamp(value: str, path: str = "timestamp") -> datetime:
    """Parse ``YYYY-MM-DDTHH:MM:SSZ`` (RFC 3339, UTC, no fraction, no offset)."""
    if not TIMESTAMP_RE.fullmatch(value):
        _bad(path, "must match YYYY-MM-DDTHH:MM:SSZ (UTC, no fractional seconds)")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        _bad(path, "is not a valid calendar date/time")


def format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_app_version(value: str) -> tuple[int, int, int]:
    if not APP_VERSION_RE.fullmatch(value):
        raise ValueError(f"{value!r} is not MAJOR.MINOR.PATCH")
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
        _bad(f"{path}.{key}", f"must be in [{lo}, {hi}]")
    return v


def _check_keys(
    obj: dict[str, JsonValue], required: frozenset[str], optional: frozenset[str], path: str
) -> None:
    missing = sorted(required - obj.keys())
    if missing:
        _bad(path, f"missing required field(s) {missing}")
    unknown = sorted(obj.keys() - required - optional)
    if unknown:
        _bad(path, f"unknown field(s) {unknown} (schema is closed)")


def _check_url(url: str, path: str) -> None:
    if not URL_RE.fullmatch(url):
        _bad(path, "must be an absolute https URL without query/fragment/userinfo")
    segments = url[len("https://") :].split("/")[1:]
    if any(s in ("", ".", "..") for s in segments):
        _bad(path, "must not contain empty, '.' or '..' path segments")


def peek_schema_version(obj: JsonValue) -> int:
    """Return ``schema_version`` without validating the rest (for ordering checks)."""
    if not isinstance(obj, dict):
        _bad("$", "manifest must be a JSON object")
    if "schema_version" not in obj:
        _bad("$", "missing required field(s) ['schema_version']")
    return _int(obj, "schema_version", "$", 1, MAX_SAFE_INTEGER)


def validate_manifest(obj: JsonValue) -> Manifest:
    """Validate a parsed manifest against schema v1. Raises E_SCHEMA*."""
    version = peek_schema_version(obj)
    assert isinstance(obj, dict)
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ManifestError(
            ErrorCode.E_SCHEMA_VERSION_UNSUPPORTED,
            f"schema_version {version} not supported (supported: "
            f"{sorted(SUPPORTED_SCHEMA_VERSIONS)})",
        )
    _check_keys(obj, REQUIRED_TOP, OPTIONAL_TOP, "$")

    sequence = _int(obj, "sequence", "$", 1, MAX_SAFE_INTEGER)
    created_at = parse_timestamp(_str(obj, "created_at", "$"), "$.created_at")
    expires_at: datetime | None = None
    if "expires_at" in obj:
        expires_at = parse_timestamp(_str(obj, "expires_at", "$"), "$.expires_at")
        if expires_at <= created_at:
            _bad("$.expires_at", "must be later than created_at")
    min_app = _str(obj, "min_app_version", "$")
    if not APP_VERSION_RE.fullmatch(min_app):
        _bad("$.min_app_version", "must be MAJOR.MINOR.PATCH (digits only)")
    key_id = _str(obj, "signer_key_id", "$")
    if not KEY_ID_RE.fullmatch(key_id):
        _bad("$.signer_key_id", "must be 16 lowercase hex characters")
    channel = _str(obj, "channel", "$")
    if channel not in CHANNELS:
        _bad("$.channel", f"must be one of {sorted(CHANNELS)}")

    for comp, keys in COMPONENT_KEYS.items():
        c = obj[comp]
        path = f"$.{comp}"
        if not isinstance(c, dict):
            _bad(path, "must be an object")
        _check_keys(c, keys, frozenset(), path)
        if not VERSION_RE.fullmatch(_str(c, "version", path)):
            _bad(f"{path}.version", "must match ^[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}$")
        if "license" in keys:
            lic = _str(c, "license", path)
            if not 1 <= len(lic) <= LICENSE_MAX_LEN or not is_printable_ascii(lic):
                _bad(f"{path}.license", f"must be 1..{LICENSE_MAX_LEN} printable ASCII chars")

    for spec in ARTIFACT_SPECS:
        c = obj[spec.component]
        assert isinstance(c, dict)
        path = f"$.{spec.component}"
        _check_url(_str(c, spec.prefix + "url", path), f"{path}.{spec.prefix}url")
        if not SHA256_RE.fullmatch(_str(c, spec.prefix + "sha256", path)):
            _bad(f"{path}.{spec.prefix}sha256", "must be 64 lowercase hex characters")
        _int(c, spec.prefix + "size_bytes", path, 1, spec.max_size)

    return Manifest(
        raw=obj,
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
