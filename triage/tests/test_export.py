"""dist/ export: determinism, checksums, --check."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from triage.export import build_artifacts, main
from triage.paths import RULES_PATH


def test_export_writes_and_check_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--rules", str(RULES_PATH), "--dist", str(tmp_path / "dist")]) == 0
    assert main(["--dist", str(tmp_path / "dist"), "--check"]) == 0
    assert "wrote" in capsys.readouterr().out


def test_check_fails_when_missing_or_stale(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dist = tmp_path / "dist"
    assert main(["--dist", str(dist), "--check"]) == 1
    main(["--dist", str(dist)])
    (dist / "rules.json").write_text("{}", encoding="utf-8")
    assert main(["--dist", str(dist), "--check"]) == 1
    assert "STALE" in capsys.readouterr().out


def test_artifacts_roundtrip_and_checksums(rules: dict[str, Any]) -> None:
    files = build_artifacts(rules)
    assert files == build_artifacts(rules)  # deterministic bytes
    assert json.loads(files["rules.json"]) == rules
    assert b"\r\n" not in files["rules.json"]
    for line in files["SHA256SUMS"].decode().splitlines():
        digest, name = line.split("  ")
        assert hashlib.sha256(files[name]).hexdigest() == digest
