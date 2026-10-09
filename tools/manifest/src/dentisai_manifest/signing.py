"""Build and sign manifests. The private key never leaves this process."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .canonical import JsonValue, canonicalize
from .errors import ErrorCode, ManifestError
from .keys import BACKEND_ROOT, key_id_of, public_b64
from .placeholders import Finding, scan_file, scan_text
from .schema import (
    CHANNEL_INTERNAL_TEST,
    CHANNEL_PRODUCTION,
    PLACEHOLDER_MARKER,
    SCHEMA_VERSION,
    URL_RE,
    VERSION_RE,
    Manifest,
    format_timestamp,
    validate_manifest,
)
from .sigfile import sign_bytes
from .verify import Policy, TrustStore, verify_manifest

MODEL_FILE: Final = "model.tflite"
LABELS_FILE: Final = "labels.json"
SHA256_FILE: Final = "SHA256"
MODEL_CARD_FILE: Final = "MODEL_CARD.md"
SUMS_FILE: Final = "SHA256SUMS"  # written by triage/recommender exporters (sha256sum format)

# Default inputs, relative to BACKEND_ROOT (backend/).
DEFAULT_MODEL_EXPORTS: Final = BACKEND_ROOT / "ml" / "exports"
DEFAULT_RULES_FILE: Final = BACKEND_ROOT / "triage" / "dist" / "rules.json"
DEFAULT_RECOMMENDER_FILE: Final = BACKEND_ROOT / "recommender" / "dist" / "recommender.json"

_SHA_LINE_RE: Final = re.compile(r"([0-9a-fA-F]{64})(?:\s+\*?(\S.*))?")


def sha256_file(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def parse_sha256_file(text: str, require: str | None = MODEL_FILE) -> dict[str, str]:
    """Parse ``sha256sum`` output. A single bare hash applies to model.tflite."""
    entries: dict[str, str] = {}
    lines = [
        ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")
    ]
    for ln in lines:
        m = _SHA_LINE_RE.fullmatch(ln)
        if not m:
            raise ManifestError(ErrorCode.E_REFUSED, f"{SHA256_FILE}: unparseable line {ln!r}")
        name = (m.group(2) or MODEL_FILE).strip().replace("\\", "/").split("/")[-1]
        entries[name] = m.group(1).lower()
    if require is not None and require not in entries:
        raise ManifestError(ErrorCode.E_REFUSED, f"{SHA256_FILE} has no entry for {require}")
    return entries


@dataclass
class BuildInputs:
    model_dir: Path
    rules_file: Path
    recommender_file: Path
    base_url: str
    sequence: int
    min_app_version: str
    created_at: datetime
    expires_at: datetime | None = None
    rules_version: str | None = None
    recommender_version: str | None = None
    model_license: str | None = None


@dataclass
class BuildResult:
    manifest: dict[str, JsonValue]
    files: list[tuple[Path, str]]  # (source, relative object path)
    findings: list[Finding] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _require_file(path: Path, what: str) -> Path:
    if not path.is_file():
        raise ManifestError(ErrorCode.E_REFUSED, f"{what} missing: {path}")
    return path


def _load_json(path: Path) -> JsonValue:
    try:
        value: JsonValue = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ManifestError(ErrorCode.E_REFUSED, f"{path} is not valid UTF-8 JSON: {exc}") from None
    return value


def _version_from(explicit: str | None, doc: JsonValue, path: Path) -> str:
    version = explicit
    if version is None and isinstance(doc, dict) and isinstance(doc.get("version"), str):
        v = doc["version"]
        assert isinstance(v, str)
        version = v
    if version is None:
        raise ManifestError(
            ErrorCode.E_REFUSED,
            f"no version: pass it explicitly or add a top-level 'version' to {path}",
        )
    if not VERSION_RE.fullmatch(version):
        raise ManifestError(ErrorCode.E_REFUSED, f"version {version!r} has invalid characters")
    return version


def resolve_input(path: Path) -> Path:
    """Absolute or CWD-relative paths that exist win; otherwise relative to backend/."""
    if path.is_absolute() or path.exists():
        return path
    return BACKEND_ROOT / path


def _check_sibling_sums(path: Path, sha: str) -> None:
    """If the exporter wrote ``SHA256SUMS`` next to ``path``, it must agree."""
    sums = path.parent / SUMS_FILE
    if not sums.is_file():
        return
    declared = parse_sha256_file(sums.read_text(encoding="utf-8"), require=None)
    if path.name in declared and declared[path.name] != sha:
        raise ManifestError(ErrorCode.E_REFUSED, f"{sums} does not match {path.name} (stale dist?)")


def license_from_labels(labels: JsonValue) -> str | None:
    """Derive the manifest license string from labels.json.

    Accepts ``"license": "<SPDX>"`` or the ml/detection/export.py layout
    ``"licenses": {"code": {"license"}, "weights": {"license"}, "datasets": [{"license"}]}``
    -> unique licenses joined with `` AND `` (code, weights, datasets order).
    Returns None when unknown/incomplete.
    """
    if not isinstance(labels, dict):
        return None
    single = labels.get("license")
    if isinstance(single, str):
        return single
    lic = labels.get("licenses")
    if not isinstance(lic, dict):
        return None
    found: list[JsonValue] = []
    for part in ("code", "weights"):
        entry = lic.get(part)
        found.append(entry.get("license") if isinstance(entry, dict) else None)
    datasets = lic.get("datasets")
    for ds in datasets if isinstance(datasets, list) else []:
        found.append(ds.get("license") if isinstance(ds, dict) else None)
    if not found or any(not isinstance(x, str) or not x for x in found):
        return None
    unique: list[str] = []
    for x in found:
        assert isinstance(x, str)
        if x not in unique:
            unique.append(x)
    return " AND ".join(unique)


def build_manifest(inp: BuildInputs) -> BuildResult:
    """Collect artifacts, compute sha256/size, scan for placeholders. Does not sign."""
    base = inp.base_url.rstrip("/")
    if not URL_RE.fullmatch(base + "/x"):
        raise ManifestError(
            ErrorCode.E_USAGE, f"base URL must be https and lowercase host: {inp.base_url}"
        )
    result = BuildResult(manifest={}, files=[])

    # --- model -----------------------------------------------------------
    model_version = inp.model_dir.resolve().name
    if not VERSION_RE.fullmatch(model_version):
        raise ManifestError(
            ErrorCode.E_REFUSED, f"model dir name {model_version!r} is not a valid version"
        )
    model = _require_file(inp.model_dir / MODEL_FILE, MODEL_FILE)
    labels = _require_file(inp.model_dir / LABELS_FILE, LABELS_FILE)
    sha_file = _require_file(inp.model_dir / SHA256_FILE, f"{SHA256_FILE} file")
    declared = parse_sha256_file(sha_file.read_text(encoding="utf-8"))
    model_sha, model_size = sha256_file(model)
    labels_sha, labels_size = sha256_file(labels)
    if declared[MODEL_FILE] != model_sha:
        raise ManifestError(ErrorCode.E_REFUSED, f"{SHA256_FILE} does not match {MODEL_FILE}")
    if LABELS_FILE in declared and declared[LABELS_FILE] != labels_sha:
        raise ManifestError(ErrorCode.E_REFUSED, f"{SHA256_FILE} does not match {LABELS_FILE}")
    labels_doc = _load_json(labels)
    result.findings += scan_file(labels)
    card = inp.model_dir / MODEL_CARD_FILE
    if card.is_file():
        result.findings += scan_file(card)
    else:
        result.warnings.append(f"{MODEL_CARD_FILE} missing in {inp.model_dir}")

    if not isinstance(labels_doc, dict):
        raise ManifestError(ErrorCode.E_REFUSED, f"{LABELS_FILE} must be a JSON object")
    meta = labels_doc.get("model")
    declared_version = meta.get("version") if isinstance(meta, dict) else None
    if declared_version is not None and declared_version != model_version:
        raise ManifestError(
            ErrorCode.E_REFUSED,
            f"labels.json model.version {declared_version!r} != dir name {model_version!r}",
        )
    license_ = inp.model_license or license_from_labels(labels_doc)
    if license_ is None:
        raise ManifestError(
            ErrorCode.E_REFUSED,
            "model license unknown: pass --model-license or complete 'license'/'licenses' "
            "in labels.json",
        )
    result.findings += scan_text(license_, "--model-license", is_json=False)

    # --- rules -----------------------------------------------------------
    rules = _require_file(inp.rules_file, "rules file")
    rules_version = _version_from(inp.rules_version, _load_json(rules), rules)
    result.findings += scan_file(rules)
    rules_sha, rules_size = sha256_file(rules)
    _check_sibling_sums(rules, rules_sha)

    # --- recommender ------------------------------------------------------
    rec = _require_file(inp.recommender_file, "recommender file")
    rec_version = _version_from(inp.recommender_version, _load_json(rec), rec)
    result.findings += scan_file(rec)
    rec_sha, rec_size = sha256_file(rec)
    _check_sibling_sums(rec, rec_sha)

    paths = {
        "model": f"model/{model_version}/{MODEL_FILE}",
        "labels": f"model/{model_version}/{LABELS_FILE}",
        "rules": f"rules/{rules_version}/rules.json",
        "rec": f"recommender/{rec_version}/recommender.json",
    }
    result.files = [
        (model, paths["model"]),
        (labels, paths["labels"]),
        (rules, paths["rules"]),
        (rec, paths["rec"]),
    ]
    m: dict[str, JsonValue] = {
        "schema_version": SCHEMA_VERSION,
        "sequence": inp.sequence,
        "created_at": format_timestamp(inp.created_at),
        "min_app_version": inp.min_app_version,
        "channel": CHANNEL_PRODUCTION,
        "model": {
            "version": model_version,
            "license": license_,
            "url": f"{base}/{paths['model']}",
            "sha256": model_sha,
            "size_bytes": model_size,
            "labels_url": f"{base}/{paths['labels']}",
            "labels_sha256": labels_sha,
            "labels_size_bytes": labels_size,
        },
        "rules": {
            "version": rules_version,
            "url": f"{base}/{paths['rules']}",
            "sha256": rules_sha,
            "size_bytes": rules_size,
        },
        "recommender": {
            "version": rec_version,
            "url": f"{base}/{paths['rec']}",
            "sha256": rec_sha,
            "size_bytes": rec_size,
        },
    }
    if inp.expires_at is not None:
        m["expires_at"] = format_timestamp(inp.expires_at)
    result.manifest = m
    return result


@dataclass(frozen=True)
class Signed:
    manifest_bytes: bytes
    sig_bytes: bytes
    manifest: Manifest
    key_id: str
    public_key_b64: str


def sign_manifest(
    private_key: Ed25519PrivateKey, doc: dict[str, JsonValue], *, allow_placeholder: bool
) -> Signed:
    """Validate, canonicalize, sign and self-verify a manifest document.

    ``allow_placeholder`` stamps channel "internal-test". Without it, a manifest
    containing PLACEHOLDER (or already marked internal-test) is signed as-is only
    if it contains no PLACEHOLDER string.
    """
    doc = dict(doc)
    kid = key_id_of(private_key.public_key())
    existing = doc.get("signer_key_id")
    if existing is None:
        doc["signer_key_id"] = kid
    elif existing != kid:
        raise ManifestError(
            ErrorCode.E_KEY_ID_MISMATCH, f"manifest signer_key_id {existing} != key {kid}"
        )
    doc.setdefault("channel", CHANNEL_PRODUCTION)
    if allow_placeholder:
        doc["channel"] = CHANNEL_INTERNAL_TEST
    elif PLACEHOLDER_MARKER in canonicalize(doc).decode("ascii"):
        raise ManifestError(
            ErrorCode.E_REFUSED, "manifest contains PLACEHOLDER; use --allow-placeholder"
        )
    manifest = validate_manifest(doc)
    raw = canonicalize(doc)
    sig = sign_bytes(private_key, raw)
    pub_b64 = public_b64(private_key.public_key())
    # Self-check with the reference verifier (catches any signer/verifier drift).
    verify_manifest(raw, sig, TrustStore.from_b64([pub_b64]), Policy(now=manifest.created_at))
    return Signed(raw, sig, manifest, kid, pub_b64)
