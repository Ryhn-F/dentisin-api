"""The committed cross-language vectors must verify and must match the generator."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from dentisai_manifest import vectors
from dentisai_manifest.canonical import canonicalize, parse_canonical, parse_strict
from dentisai_manifest.errors import ManifestError
from dentisai_manifest.keys import TEST_KEY_IDS
from dentisai_manifest.schema import parse_timestamp
from dentisai_manifest.verify import Policy, TrustStore, verify_manifest

VDIR = vectors.VECTOR_DIR
VALID = sorted(VDIR.glob("manifest_v1_valid_*.json"))


def load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def outcome(raw: bytes, sig: bytes, case: dict[str, Any]) -> str:
    pol = Policy(
        now=parse_timestamp(case["now"]),
        production=case.get("production", False),
        last_sequence=case.get("last_sequence"),
        last_manifest_sha256=case.get("last_manifest_sha256"),
        last_schema_version=case.get("last_schema_version"),
        app_version=case.get("app_version"),
    )
    trust = TrustStore.from_b64(case["trusted_public_keys_b64"], case.get("revoked_key_ids", []))
    try:
        verify_manifest(raw, sig, trust, pol)
    except ManifestError as exc:
        return str(exc.code)
    return "OK"


def test_committed_vectors_match_generator() -> None:
    rendered = vectors.render_all()
    assert len(VALID) >= 3
    assert sorted(p.name for p in VDIR.glob("*.json")) == sorted(rendered)
    for name, text in rendered.items():
        on_disk = (VDIR / name).read_bytes().decode("utf-8")
        assert on_disk == text, (
            f"{name} drifted; regenerate with python -m dentisai_manifest.vectors"
        )


def test_write_all_to_tmp(tmp_path: Path) -> None:
    written = vectors.write_all(tmp_path)
    assert {p.name for p in written} == {p.name for p in VDIR.glob("*.json")}


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.name)
def test_valid_vector(path: Path) -> None:
    v = load(path)
    assert v["test_only"] is True
    assert v["signing_key"]["key_id"] in TEST_KEY_IDS
    raw = bytes.fromhex(v["canonical_hex"])
    assert raw == v["manifest_json"].encode("ascii")
    assert canonicalize(v["manifest_object_unsorted"]) == raw
    assert canonicalize(parse_canonical(raw)) == raw
    assert hashlib.sha256(raw).hexdigest() == v["canonical_sha256"]
    sig = v["manifest_sig"].encode("ascii")
    assert json.loads(sig)["sig"]
    expectations = {c["name"]: c["expect"] for c in v["cases"]}
    assert expectations["default policy"] == "OK"
    assert expectations["production policy rejects TEST key"] == "E_KEY_TEST_ONLY"
    for case in v["cases"]:
        assert outcome(raw, sig, case) == case["expect"], case["name"]


def test_invalid_vectors() -> None:
    v = load(VDIR / "manifest_v1_invalid.json")
    assert len(v["entries"]) >= 15
    for e in v["entries"]:
        assert e["expect"] != "OK", e["name"]
        got = outcome(bytes.fromhex(e["manifest_hex"]), bytes.fromhex(e["manifest_sig_hex"]),
                      v["policy"])  # fmt: skip
        assert got == e["expect"], e["name"]


def test_canonicalization_vectors() -> None:
    v = load(VDIR / "canonicalization.json")
    for c in v["canonicalize"]:
        assert canonicalize(parse_strict(c["input"])).decode() == c["canonical"]
    for c in v["parse_canonical"]:
        raw = bytes.fromhex(c["bytes_hex"])
        try:
            parse_canonical(raw)
            got = "OK"
        except ManifestError as exc:
            got = str(exc.code)
        assert got == c["expect"], c["name"]
