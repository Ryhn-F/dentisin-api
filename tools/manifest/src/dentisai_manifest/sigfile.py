"""``manifest.sig`` format: canonical JSON {"alg":"Ed25519","key_id":...,"sig":...}."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Final

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .canonical import JsonValue, canonicalize, parse_canonical
from .errors import ErrorCode, ManifestError
from .keys import b64decode_canonical, key_id_of
from .schema import KEY_ID_RE, MAX_SIG_BYTES

ALG: Final = "Ed25519"
SIG_KEYS: Final = frozenset({"alg", "key_id", "sig"})


@dataclass(frozen=True)
class SignatureFile:
    key_id: str
    signature: bytes  # raw 64 bytes


def sign_bytes(private_key: Ed25519PrivateKey, data: bytes) -> bytes:
    """Return the canonical manifest.sig bytes for ``data`` (the exact manifest bytes)."""
    sig = private_key.sign(data)
    doc: dict[str, JsonValue] = {
        "alg": ALG,
        "key_id": key_id_of(private_key.public_key()),
        "sig": base64.b64encode(sig).decode("ascii"),
    }
    return canonicalize(doc)


def parse_sig_file(raw: bytes) -> SignatureFile:
    if len(raw) > MAX_SIG_BYTES:
        raise ManifestError(
            ErrorCode.E_TOO_LARGE, f"manifest.sig larger than {MAX_SIG_BYTES} bytes"
        )
    try:
        doc = parse_canonical(raw)
    except ManifestError as exc:
        raise ManifestError(ErrorCode.E_SIG_FORMAT, f"manifest.sig: {exc.message}") from None
    if not isinstance(doc, dict) or set(doc.keys()) != SIG_KEYS:
        raise ManifestError(
            ErrorCode.E_SIG_FORMAT, f"manifest.sig must have exactly keys {sorted(SIG_KEYS)}"
        )
    alg, key_id, sig = doc["alg"], doc["key_id"], doc["sig"]
    if alg != ALG:
        raise ManifestError(ErrorCode.E_SIG_FORMAT, f"unsupported alg {alg!r}")
    if not isinstance(key_id, str) or not KEY_ID_RE.fullmatch(key_id):
        raise ManifestError(ErrorCode.E_SIG_FORMAT, "key_id must be 16 lowercase hex characters")
    if not isinstance(sig, str):
        raise ManifestError(ErrorCode.E_SIG_FORMAT, "sig must be a string")
    try:
        signature = b64decode_canonical(sig, 64, "sig")
    except ValueError as exc:
        raise ManifestError(ErrorCode.E_SIG_FORMAT, str(exc)) from None
    return SignatureFile(key_id, signature)
