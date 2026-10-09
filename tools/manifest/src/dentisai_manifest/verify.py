"""Reference verifier. The TypeScript app MUST implement the same steps in the
same order (SPEC.md section 5). Any failure rejects the whole manifest; the
caller keeps its last-good state and never applies a partial update.
"""

from __future__ import annotations

import hashlib
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Final, Protocol

from cryptography.exceptions import InvalidSignature

from .canonical import JsonValue, parse_canonical
from .errors import ErrorCode, ManifestError
from .keys import TEST_KEY_IDS, TrustedKey, load_public_key_b64
from .schema import (
    CHANNEL_PRODUCTION,
    MAX_MANIFEST_BYTES,
    MAX_SIG_BYTES,
    ArtifactRef,
    Manifest,
    contains_placeholder,
    parse_app_version,
    peek_schema_version,
    validate_manifest,
)
from .sigfile import parse_sig_file

CHUNK: Final = 64 * 1024
DEFAULT_TIMEOUT_S: Final = 30.0
USER_AGENT: Final = "dentisai-manifest-verify/0.1"


@dataclass(frozen=True)
class TrustStore:
    keys: dict[str, TrustedKey]
    revoked: frozenset[str] = frozenset()

    @classmethod
    def from_b64(cls, public_keys: Iterable[str], revoked: Iterable[str] = ()) -> TrustStore:
        keys: dict[str, TrustedKey] = {}
        for text in public_keys:
            tk = load_public_key_b64(text)
            keys[tk.key_id] = tk
        return cls(keys, frozenset(revoked))


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
) -> Manifest:
    """Verify manifest bytes + signature bytes. Returns the validated manifest or raises."""
    # 1. Size caps before any parsing.
    if len(raw_manifest) > MAX_MANIFEST_BYTES:
        raise ManifestError(
            ErrorCode.E_TOO_LARGE, f"manifest.json larger than {MAX_MANIFEST_BYTES} bytes"
        )
    if len(raw_sig) > MAX_SIG_BYTES:
        raise ManifestError(
            ErrorCode.E_TOO_LARGE, f"manifest.sig larger than {MAX_SIG_BYTES} bytes"
        )
    # 2. Signature file.
    sig = parse_sig_file(raw_sig)
    # 3. Key selection (revocation wins over trust).
    if sig.key_id in trust.revoked:
        raise ManifestError(ErrorCode.E_KEY_REVOKED, f"key_id {sig.key_id} is revoked")
    if policy.production and sig.key_id in TEST_KEY_IDS:
        raise ManifestError(
            ErrorCode.E_KEY_TEST_ONLY, f"key_id {sig.key_id} is the public TEST key"
        )
    trusted = trust.keys.get(sig.key_id)
    if trusted is None:
        raise ManifestError(ErrorCode.E_KEY_UNKNOWN, f"key_id {sig.key_id} is not trusted")
    # 4. Ed25519 over the exact received bytes (verify-before-parse).
    try:
        trusted.public_key.verify(sig.signature, raw_manifest)
    except InvalidSignature:
        raise ManifestError(ErrorCode.E_SIGNATURE, "Ed25519 signature does not verify") from None
    # 5. Bytes must be exactly canonical (rejects dup keys, floats, whitespace, ...).
    doc = parse_canonical(raw_manifest)
    # 6. schema_version downgrade / support.
    version = peek_schema_version(doc)
    if policy.last_schema_version is not None and version < policy.last_schema_version:
        raise ManifestError(
            ErrorCode.E_SCHEMA_DOWNGRADE,
            f"schema_version {version} < last seen {policy.last_schema_version}",
        )
    # 7. Closed schema.
    manifest = validate_manifest(doc)
    # 8. Signed key id must match the key that verified.
    if manifest.signer_key_id != sig.key_id:
        raise ManifestError(
            ErrorCode.E_KEY_ID_MISMATCH,
            f"signer_key_id {manifest.signer_key_id} != manifest.sig key_id {sig.key_id}",
        )
    # 9. Channel.
    if policy.production:
        if manifest.channel != CHANNEL_PRODUCTION:
            raise ManifestError(
                ErrorCode.E_CHANNEL, f"channel {manifest.channel!r} rejected in production"
            )
        if contains_placeholder(doc):
            raise ManifestError(ErrorCode.E_PLACEHOLDER, "manifest contains PLACEHOLDER")
    # 10. Expiry.
    if manifest.expires_at is not None and policy.now >= manifest.expires_at:
        raise ManifestError(ErrorCode.E_EXPIRED, "manifest expired")
    # 11. Rollback protection.
    if policy.last_sequence is not None:
        if manifest.sequence < policy.last_sequence:
            raise ManifestError(
                ErrorCode.E_ROLLBACK,
                f"sequence {manifest.sequence} < last seen {policy.last_sequence}",
            )
        if (
            manifest.sequence == policy.last_sequence
            and policy.last_manifest_sha256 is not None
            and hashlib.sha256(raw_manifest).hexdigest() != policy.last_manifest_sha256
        ):
            raise ManifestError(
                ErrorCode.E_ROLLBACK, "same sequence as last seen but different manifest bytes"
            )
    # 12. Minimum app version.
    if policy.app_version is not None and parse_app_version(policy.app_version) < parse_app_version(
        manifest.min_app_version
    ):
        raise ManifestError(
            ErrorCode.E_APP_TOO_OLD,
            f"app {policy.app_version} < min_app_version {manifest.min_app_version}",
        )
    return manifest


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


@dataclass
class ArtifactResult:
    name: str
    url: str
    source: str
    expected_sha256: str
    expected_size: int
    ok: bool = False
    actual_sha256: str | None = None
    actual_size: int | None = None
    code: str | None = None
    message: str | None = None

    def to_json(self) -> dict[str, JsonValue]:
        return {k: v for k, v in self.__dict__.items()}


def _hash_capped(read: Callable[[int], bytes], expected_size: int) -> tuple[str, int]:
    """Hash at most expected_size + 1 bytes (enough to detect oversize)."""
    h = hashlib.sha256()
    total = 0
    limit = expected_size + 1
    while total < limit:
        chunk = read(min(CHUNK, limit - total))
        if not chunk:
            break
        h.update(chunk)
        total += len(chunk)
    return h.hexdigest(), total


def _finish(result: ArtifactResult, sha: str, size: int) -> ArtifactResult:
    result.actual_sha256, result.actual_size = sha, size
    if size != result.expected_size:
        result.code = ErrorCode.E_ARTIFACT_SIZE
        more = "+" if size > result.expected_size else ""
        result.message = f"size {size}{more} != expected {result.expected_size}"
    elif sha != result.expected_sha256:
        result.code = ErrorCode.E_ARTIFACT_HASH
        result.message = "sha256 mismatch"
    else:
        result.ok = True
    return result


def common_base_url(refs: list[ArtifactRef]) -> str:
    prefix = os.path.commonprefix([r.url for r in refs])
    return prefix[: prefix.rfind("/") + 1]


def check_local(
    manifest: Manifest, artifacts_dir: Path, base_url: str | None = None
) -> list[ArtifactResult]:
    """Check artifacts against a local mirror of the bucket (url minus base_url)."""
    refs = manifest.artifacts()
    base = base_url if base_url is not None else common_base_url(refs)
    if base and not base.endswith("/"):
        base += "/"
    root = artifacts_dir.resolve()
    results: list[ArtifactResult] = []
    for ref in refs:
        res = ArtifactResult(ref.name, ref.url, "", ref.sha256, ref.size_bytes)
        results.append(res)
        if not ref.url.startswith(base):
            res.code, res.message = (
                ErrorCode.E_ARTIFACT_MISSING,
                f"url does not start with base {base}",
            )
            continue
        rel = urllib.parse.unquote(ref.url[len(base) :])
        path = (root / rel).resolve()
        res.source = str(path)
        if not path.is_relative_to(root):  # pragma: no cover - schema forbids '..'
            res.code, res.message = ErrorCode.E_ARTIFACT_MISSING, "path escapes artifacts dir"
            continue
        if not path.is_file():
            res.code, res.message = ErrorCode.E_ARTIFACT_MISSING, "file not found"
            continue
        with path.open("rb") as fh:
            sha, size = _hash_capped(fh.read, ref.size_bytes)
        _finish(res, sha, size)
    return results


class _Response(Protocol):
    def read(self, n: int = -1, /) -> bytes: ...
    def geturl(self) -> str: ...
    def getheader(self, name: str, default: str | None = None, /) -> str | None: ...
    def close(self) -> None: ...


Opener = Callable[[urllib.request.Request, float], _Response]


def _default_opener(req: urllib.request.Request, timeout: float) -> _Response:
    resp: _Response = urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - https enforced
    return resp


def fetch_capped(url: str, cap: int, timeout: float, opener: Opener | None = None) -> bytes:
    """Download ``url`` (https only) refusing more than ``cap`` bytes."""
    if not url.startswith("https://"):
        raise ManifestError(ErrorCode.E_ARTIFACT_FETCH, f"refusing non-https URL {url}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    try:
        resp = (opener or _default_opener)(req, timeout)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ManifestError(ErrorCode.E_ARTIFACT_FETCH, f"{url}: {exc}") from None
    try:
        if not resp.geturl().startswith("https://"):
            raise ManifestError(ErrorCode.E_ARTIFACT_FETCH, f"{url}: redirected to non-https URL")
        buf = bytearray()
        while len(buf) <= cap:
            chunk = resp.read(min(CHUNK, cap + 1 - len(buf)))
            if not chunk:
                break
            buf += chunk
        if len(buf) > cap:
            raise ManifestError(ErrorCode.E_TOO_LARGE, f"{url}: larger than {cap} bytes")
        return bytes(buf)
    except (TimeoutError, OSError) as exc:
        raise ManifestError(ErrorCode.E_ARTIFACT_FETCH, f"{url}: {exc}") from None
    finally:
        resp.close()


def check_remote(
    manifest: Manifest, timeout: float = DEFAULT_TIMEOUT_S, opener: Opener | None = None
) -> list[ArtifactResult]:
    results: list[ArtifactResult] = []
    for ref in manifest.artifacts():
        res = ArtifactResult(ref.name, ref.url, ref.url, ref.sha256, ref.size_bytes)
        results.append(res)
        req = urllib.request.Request(ref.url, headers={"User-Agent": USER_AGENT})  # noqa: S310
        try:
            resp = (opener or _default_opener)(req, timeout)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            res.code, res.message = ErrorCode.E_ARTIFACT_FETCH, str(exc)
            continue
        try:
            if not resp.geturl().startswith("https://"):
                res.code, res.message = ErrorCode.E_ARTIFACT_FETCH, "redirected to non-https URL"
                continue
            declared = resp.getheader("Content-Length")
            if declared is not None and declared.isdigit() and int(declared) > ref.size_bytes:
                res.code = ErrorCode.E_ARTIFACT_SIZE
                res.message = f"Content-Length {declared} > expected {ref.size_bytes}"
                continue
            sha, size = _hash_capped(resp.read, ref.size_bytes)
            _finish(res, sha, size)
        except (TimeoutError, OSError) as exc:
            res.code, res.message = ErrorCode.E_ARTIFACT_FETCH, str(exc)
        finally:
            resp.close()
    return results


def first_failure(results: list[ArtifactResult]) -> ArtifactResult | None:
    return next((r for r in results if not r.ok), None)


@dataclass
class Report:
    ok: bool
    exit_code: int
    error: dict[str, str] | None = None
    manifest: dict[str, JsonValue] | None = None
    manifest_sha256: str | None = None
    artifacts: list[dict[str, JsonValue]] = field(default_factory=list)

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "error": dict(self.error) if self.error else None,
            "manifest": self.manifest,
            "manifest_sha256": self.manifest_sha256,
            "artifacts": list(self.artifacts),
        }


def read_binary(fh: BinaryIO, cap: int) -> bytes:
    data = fh.read(cap + 1)
    if len(data) > cap:
        raise ManifestError(ErrorCode.E_TOO_LARGE, f"input larger than {cap} bytes")
    return data
