"""Ed25519 key handling: key_id, loading, generation, location sanity checks.

Rules (SPEC.md section 6):
- key_id = lowercase hex of the first 8 bytes of SHA-256(raw 32-byte public key).
- Private keys are never printed, logged or embedded in error messages.
- keygen refuses to write inside this repository or any git work tree and
  never overwrites an existing file.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .errors import ErrorCode, ManifestError

ENV_SIGNING_KEY_PATH: Final = "MANIFEST_SIGNING_KEY_PATH"
ENV_PUBLIC_KEY_B64: Final = "MANIFEST_PUBLIC_KEY_B64"
ENV_PASSPHRASE: Final = "MANIFEST_SIGNING_KEY_PASSPHRASE"  # noqa: S105 - env var name

# backend/tools/manifest/src/dentisai_manifest/keys.py -> parents[4] == backend/
BACKEND_ROOT: Final = Path(__file__).resolve().parents[4]
ENV_FILE: Final = BACKEND_ROOT / ".env"
_MONOREPO_MARKERS: Final = ("CLAUDE.md", ".git", "app")


def protected_roots(backend_root: Path | None = None) -> list[Path]:
    """Directory trees where private keys must never be written.

    Always backend/ itself; also its parent when that parent looks like the
    DentisAI monorepo (contains CLAUDE.md, .git or app/). backend/ is meant to be
    movable as an independent folder, so the parent is not assumed blindly.
    """
    root = backend_root or BACKEND_ROOT
    roots = [root]
    parent = root.parent
    if parent != root and any((parent / m).exists() for m in _MONOREPO_MARKERS):
        roots.append(parent)
    return roots


def env_value(name: str, env_file: Path | None = None) -> str | None:
    """os.environ first, then a ``NAME=value`` line in backend/.env. Never logged."""
    value = os.environ.get(name)
    if value:
        return value
    path = env_file or ENV_FILE
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].lstrip()
        key, sep, val = stripped.partition("=")
        if sep and key.strip() == name:
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
                val = val[1:-1]
            return val or None
    return None


MAX_KEY_FILE_BYTES: Final = 4096

# ---------------------------------------------------------------------------
# TEST-ONLY key. Derived from a public label, so it is NOT secret and must never
# be trusted by a production app. Used only for the published test vectors.
# ---------------------------------------------------------------------------
TEST_KEY_LABELS: Final = (
    b"DentisAI manifest test vector key 1 - TEST ONLY - NEVER TRUST IN PRODUCTION",
    b"DentisAI manifest test vector key 2 - TEST ONLY - NEVER TRUST IN PRODUCTION",
)


def fixed_test_key(index: int = 0) -> Ed25519PrivateKey:
    """Deterministic TEST-ONLY key: seed = SHA-256(TEST_KEY_LABELS[index])."""
    return Ed25519PrivateKey.from_private_bytes(hashlib.sha256(TEST_KEY_LABELS[index]).digest())


def public_raw(key: Ed25519PublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def key_id_of(public_key: Ed25519PublicKey | bytes) -> str:
    raw = public_key if isinstance(public_key, bytes) else public_raw(public_key)
    if len(raw) != 32:
        raise ValueError("Ed25519 public key must be 32 bytes")
    return hashlib.sha256(raw).digest()[:8].hex()


def public_b64(key: Ed25519PublicKey) -> str:
    return base64.b64encode(public_raw(key)).decode("ascii")


TEST_KEY_IDS: Final = frozenset(
    key_id_of(fixed_test_key(i).public_key()) for i in range(len(TEST_KEY_LABELS))
)


def b64decode_canonical(text: str, expected_len: int, what: str) -> bytes:
    """Strict RFC 4648 section 4 base64 (with padding) that must round-trip."""
    try:
        raw = base64.b64decode(text.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError, ValueError):
        raise ValueError(f"{what} is not valid base64") from None
    if base64.b64encode(raw).decode("ascii") != text:
        raise ValueError(f"{what} is not canonical base64")
    if len(raw) != expected_len:
        raise ValueError(f"{what} must decode to {expected_len} bytes, got {len(raw)}")
    return raw


@dataclass(frozen=True)
class TrustedKey:
    key_id: str
    public_key: Ed25519PublicKey


def load_public_key_b64(text: str) -> TrustedKey:
    raw = b64decode_canonical(text.strip(), 32, "public key")
    try:
        pk = Ed25519PublicKey.from_public_bytes(raw)
    except ValueError:  # pragma: no cover - from_public_bytes accepts any 32 bytes
        raise ValueError("public key is not a valid Ed25519 point") from None
    return TrustedKey(key_id_of(raw), pk)


def _is_within(path: Path, root: Path) -> bool:
    p = os.path.normcase(str(path))
    r = os.path.normcase(str(root))
    return p == r or p.startswith(r.rstrip("\\/") + os.sep)


def _git_worktree_ancestor(path: Path) -> Path | None:
    for parent in [path, *path.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def location_problems(path: Path) -> list[str]:
    """Reasons why ``path`` is an unsafe place for a private key."""
    resolved = path.expanduser().resolve(strict=False)
    problems: list[str] = []
    for root in protected_roots():
        if _is_within(resolved, root):
            problems.append(f"path is inside the DentisAI repository ({root})")
            break
    git_root = _git_worktree_ancestor(resolved.parent)
    if git_root is not None:
        problems.append(f"path is inside a git work tree ({git_root})")
    return problems


def permission_problems(path: Path) -> list[str]:
    if os.name != "posix":  # pragma: no cover - ACLs are not inspected on Windows
        return []
    mode = path.stat().st_mode  # pragma: no cover
    if mode & (stat.S_IRWXG | stat.S_IRWXO):  # pragma: no cover
        return [f"key file is accessible by group/others (mode {oct(mode & 0o777)}); chmod 600"]
    return []  # pragma: no cover


def _passphrase_from_env() -> bytes | None:
    value = os.environ.get(ENV_PASSPHRASE)
    return value.encode("utf-8") if value else None


@dataclass(frozen=True)
class LoadedKey:
    private_key: Ed25519PrivateKey
    key_id: str
    encrypted_at_rest: bool
    warnings: tuple[str, ...]


def load_private_key(path: Path, passphrase: bytes | None = None) -> LoadedKey:
    """Load a PKCS#8 PEM (optionally encrypted) or raw 32-byte base64 seed.

    Error messages never include file contents.
    """
    try:
        if path.stat().st_size > MAX_KEY_FILE_BYTES:
            raise ManifestError(ErrorCode.E_REFUSED, f"key file {path} is too large")
        data = path.read_bytes()
    except OSError as exc:
        raise ManifestError(
            ErrorCode.E_IO, f"cannot read key file {path}: {exc.strerror}"
        ) from None

    warnings = [*location_problems(path), *permission_problems(path)]
    encrypted = False
    key: Ed25519PrivateKey
    if data.lstrip().startswith(b"-----BEGIN"):
        encrypted = b"ENCRYPTED" in data
        pw = passphrase if passphrase is not None else _passphrase_from_env()
        if encrypted and pw is None:
            raise ManifestError(
                ErrorCode.E_USAGE,
                f"key file is encrypted; set {ENV_PASSPHRASE} or use --prompt-passphrase",
            )
        try:
            loaded = serialization.load_pem_private_key(data, password=pw if encrypted else None)
        except (ValueError, TypeError):
            raise ManifestError(
                ErrorCode.E_REFUSED, "key file could not be decoded (wrong format or passphrase)"
            ) from None
        if not isinstance(loaded, Ed25519PrivateKey):
            raise ManifestError(ErrorCode.E_REFUSED, "key file is not an Ed25519 private key")
        key = loaded
    else:
        try:
            seed = b64decode_canonical(data.decode("ascii").strip(), 32, "raw private key")
        except (ValueError, UnicodeDecodeError):
            raise ManifestError(
                ErrorCode.E_REFUSED,
                "key file is neither PKCS#8 PEM nor base64 of a raw 32-byte Ed25519 seed",
            ) from None
        key = Ed25519PrivateKey.from_private_bytes(seed)
    if not encrypted:
        warnings.append("private key is not encrypted at rest (consider an encrypted PKCS#8 PEM)")
    return LoadedKey(key, key_id_of(key.public_key()), encrypted, tuple(warnings))


def keygen(out_path: Path, passphrase: bytes | None) -> tuple[str, str]:
    """Generate a key, write the private PEM to ``out_path``; return (key_id, public_b64).

    Refuses locations inside the repository / any git work tree and refuses to
    overwrite. The file is created with O_EXCL and mode 0600 (POSIX).
    """
    target = out_path.expanduser()
    problems = location_problems(target)
    if problems:
        raise ManifestError(
            ErrorCode.E_REFUSED, "refusing to write private key: " + "; ".join(problems)
        )
    if not target.parent.is_dir():
        raise ManifestError(ErrorCode.E_IO, f"directory {target.parent} does not exist")
    key = Ed25519PrivateKey.generate()
    enc: serialization.KeySerializationEncryption = (
        serialization.BestAvailableEncryption(passphrase)
        if passphrase
        else serialization.NoEncryption()
    )
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, enc)
    try:
        fd = os.open(
            target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600
        )
    except FileExistsError:
        raise ManifestError(
            ErrorCode.E_REFUSED, f"refusing to overwrite existing file {target}"
        ) from None
    except OSError as exc:
        raise ManifestError(ErrorCode.E_IO, f"cannot create {target}: {exc.strerror}") from None
    with os.fdopen(fd, "wb") as fh:
        fh.write(pem)
    pub = key.public_key()
    return key_id_of(pub), public_b64(pub)
