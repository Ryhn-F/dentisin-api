"""Parity of app.services.manifest_verify with the published vectors of the
manifest tooling (backend/tools/manifest/vectors). Every case must yield the
same OK / E_* code as the reference verifier."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from app.services import manifest_verify as mv

from .conftest import VECTORS_DIR

pytestmark = pytest.mark.skipif(not VECTORS_DIR.is_dir(), reason="manifest vectors not present")


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((VECTORS_DIR / name).read_text("utf-8"))
    return data


def _ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _outcome(raw: bytes, sig: bytes, case: dict[str, Any]) -> str:
    trust = mv.TrustStore.from_b64(case["trusted_public_keys_b64"], case.get("revoked_key_ids", ()))
    policy = mv.Policy(
        now=_ts(case["now"]),
        production=bool(case.get("production", False)),
        last_sequence=case.get("last_sequence"),
        last_manifest_sha256=case.get("last_manifest_sha256"),
        last_schema_version=case.get("last_schema_version"),
        app_version=case.get("app_version"),
    )
    try:
        mv.verify_manifest(raw, sig, trust, policy)
    except mv.ManifestError as exc:
        return exc.code
    return "OK"


def _valid_files() -> list[Path]:
    return sorted(VECTORS_DIR.glob("manifest_v1_valid_*.json")) if VECTORS_DIR.is_dir() else []


@pytest.mark.parametrize("path", _valid_files(), ids=lambda p: p.name)
def test_valid_vectors(path: Path) -> None:
    vector = _load(path.name)
    raw = vector["manifest_json"].encode("ascii")
    assert raw.hex() == vector["canonical_hex"]
    assert hashlib.sha256(raw).hexdigest() == vector["canonical_sha256"]
    # our canonicalizer reproduces the exact signed bytes from the unsorted object
    assert mv.canonicalize(vector["manifest_object_unsorted"]) == raw
    sig = vector["manifest_sig"].encode("ascii")
    for case in vector["cases"]:
        assert _outcome(raw, sig, case) == case["expect"], case["name"]


def test_invalid_vectors() -> None:
    vector = _load("manifest_v1_invalid.json")
    policy_case = vector["policy"]
    for entry in vector["entries"]:
        raw = bytes.fromhex(entry["manifest_hex"])
        sig = bytes.fromhex(entry["manifest_sig_hex"])
        assert _outcome(raw, sig, policy_case) == entry["expect"], entry["name"]


def test_canonicalization_vectors() -> None:
    vector = _load("canonicalization.json")
    for case in vector["canonicalize"]:
        value = mv.parse_strict(case["input"])
        assert mv.canonicalize(value).decode("ascii") == case["canonical"], case["name"]
    for case in vector["parse_canonical"]:
        raw = bytes.fromhex(case["bytes_hex"])
        try:
            mv.parse_canonical(raw)
            got = "OK"
        except mv.ManifestError as exc:
            got = exc.code
        assert got == case["expect"], case["name"]


def test_test_key_ids_match_vectors() -> None:
    vector = _load("manifest_v1_valid_01_minimal.json")
    assert {vector["signing_key"]["key_id"], vector["other_key"]["key_id"]} == mv.TEST_KEY_IDS
