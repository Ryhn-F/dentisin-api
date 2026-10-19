from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest

from contracts import build_contracts as bc

BACKEND = Path(__file__).resolve().parents[2]


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def write(root: Path, rel: str, data: bytes) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def spec_bytes(files: list[dict[str, Any]], version: str = "1.2.3") -> bytes:
    return json.dumps(
        {"bundle_name": "dentisai-contracts", "bundle_version": version, "files": files}
    ).encode()


RULES = b'{"version": "0.9.0", "status": "APPROVED"}\n'
CASES = b'{"format_version": 2, "cases": [{}, {}]}\n'


def mini_repo(root: Path, rules: bytes = RULES) -> bc.Spec:
    write(root, "triage/dist/rules.json", rules)
    write(root, "triage/dist/SHA256SUMS", f"{sha(RULES)}  rules.json\n".encode())
    write(root, "triage/tests/cases.json", CASES)
    write(root, "docs/style/forbidden_phrases.md", b"# phrases\n")
    return bc.load_spec(spec_bytes([
        {"path": "triage/dist/rules.json", "component": "triage", "role": "artifact"},
        {"path": "triage/dist/SHA256SUMS", "component": "triage", "role": "checksum"},
        {"path": "triage/tests/cases.json", "component": "triage", "role": "parity"},
        {"path": "docs/style/forbidden_phrases.md", "component": "style", "role": "lint"},
    ]))


def rezip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def unzip(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


# ----------------------------------------------------------------------------- the real bundle
def test_real_spec_builds_and_verifies_from_worktree() -> None:
    spec = bc.load_spec(bc.DEFAULT_SPEC.read_bytes())
    data, index = bc.build(spec, bc.WorktreeSource(BACKEND))
    assert bc.verify(data) == json.loads(bc.dumps(index))
    assert {f["path"] for f in index["files"]} == {e.path for e in spec.files}
    assert set(index["components"]) == {
        "triage", "recommender", "manifest", "detection", "quality_gate", "brushing"
    }
    # As long as the dentist has not signed off, the bundle must say so.
    assert index["clinical_status"] == "draft"
    assert "triage" in index["clinical_blockers"]


def test_real_spec_lists_the_parity_contracts_the_app_needs() -> None:
    paths = {e.path for e in bc.load_spec(bc.DEFAULT_SPEC.read_bytes()).files}
    for required in (
        "triage/dist/rules.json", "triage/tests/cases.json", "recommender/dist/recommender.json",
        "recommender/tests/cases.json", "ml/detection/tests/decode_vectors.json",
        "ml/detection/tests/quality_gate_vectors.json", "ml/brushing/tests/cases.json",
        "tools/manifest/vectors/manifest_v1_invalid.json", "docs/style/forbidden_phrases.md",
    ):
        assert required in paths
    # Never ship data, weights, secrets or private keys.
    assert not any(p.startswith(("ml/data/", "ml/exports/")) or p.endswith((".pem", ".env"))
                   for p in paths)


# ------------------------------------------------------------------------------------- build
def test_build_is_deterministic_and_zip_metadata_fixed(tmp_path: Path) -> None:
    spec = mini_repo(tmp_path)
    a, _ = bc.build(spec, bc.WorktreeSource(tmp_path))
    b, _ = bc.build(spec, bc.WorktreeSource(tmp_path))
    assert a == b
    with zipfile.ZipFile(io.BytesIO(a)) as zf:
        infos = zf.infolist()
        assert infos[0].filename == "dentisai-contracts-1.2.3/CONTRACTS.json"
        assert [i.filename for i in infos[1:]] == sorted(i.filename for i in infos[1:])
        assert all(i.date_time == bc.ZIP_DATE and i.create_system == 3 for i in infos)


def test_index_content(tmp_path: Path) -> None:
    _, index = bc.build(mini_repo(tmp_path), bc.WorktreeSource(tmp_path))
    assert index["components"]["triage"] == {
        "rules_version": "0.9.0", "status": "APPROVED", "cases_format": 2, "cases": 2
    }
    assert index["clinical_status"] == "approved" and index["clinical_blockers"] == []
    entry = next(f for f in index["files"] if f["path"] == "triage/dist/rules.json")
    assert entry == {"path": "triage/dist/rules.json", "component": "triage", "role": "artifact",
                     "test_only": False, "sha256": sha(RULES), "size": len(RULES)}


def test_stale_checksum_file_is_refused(tmp_path: Path) -> None:
    spec = mini_repo(tmp_path, rules=b'{"version": "1.0.0", "status": "DRAFT"}\n')
    with pytest.raises(bc.ContractError, match="checksum mismatch for triage/dist/rules.json"):
        bc.build(spec, bc.WorktreeSource(tmp_path))


def test_checksum_listing_unbundled_file_is_refused(tmp_path: Path) -> None:
    spec = mini_repo(tmp_path)
    sums = f"{sha(RULES)}  rules.json\n{'0' * 64}  x.json\n"
    write(tmp_path, "triage/dist/SHA256SUMS", sums.encode())
    with pytest.raises(bc.ContractError, match="not in the bundle"):
        bc.build(spec, bc.WorktreeSource(tmp_path))


def test_missing_and_empty_files_are_refused(tmp_path: Path) -> None:
    spec = mini_repo(tmp_path)
    (tmp_path / "triage/tests/cases.json").unlink()
    with pytest.raises(bc.ContractError, match="missing file: triage/tests/cases.json"):
        bc.build(spec, bc.WorktreeSource(tmp_path))
    write(tmp_path, "triage/tests/cases.json", b"")
    with pytest.raises(bc.ContractError, match="empty file"):
        bc.build(spec, bc.WorktreeSource(tmp_path))


def test_draft_components_are_blockers() -> None:
    comps: dict[str, dict[str, Any]] = {
        "triage": {"status": "APPROVED"}, "recommender": {"release_ready": True},
        "quality_gate": {"status": "FIXED"}, "brushing": {"status": "PLACEHOLDER"},
    }
    assert bc.clinical_blockers(comps) == ["brushing"]
    comps["recommender"]["release_ready"] = False
    assert bc.clinical_blockers(comps) == ["recommender", "brushing"]


def test_yaml_scalar_reads_quoted_and_commented_values() -> None:
    c = {"x.yaml": b'# version: 9\nversion: "0.1.0"  # semver\nstatus: PLACEHOLDER\n'}
    assert bc._yaml_scalar(c, "x.yaml", "version") == "0.1.0"
    assert bc._yaml_scalar(c, "x.yaml", "status") == "PLACEHOLDER"
    assert bc._yaml_scalar(c, "x.yaml", "missing") is None


# -------------------------------------------------------------------------------------- spec
@pytest.mark.parametrize(
    ("raw", "match"),
    [
        (b"[]", "top level"),
        (spec_bytes([], "1.0"), "MAJOR.MINOR.PATCH"),
        (spec_bytes([]), "non-empty"),
        (spec_bytes([{"path": "../etc/x", "component": "a", "role": "spec"}]), "path invalid"),
        (spec_bytes([{"path": "/abs", "component": "a", "role": "spec"}]), "path invalid"),
        (spec_bytes([{"path": "CONTRACTS.json", "component": "a", "role": "spec"}]), "reserved"),
        (spec_bytes([{"path": "a.md", "component": "a", "role": "spec"}] * 2), "duplicate"),
        (spec_bytes([{"path": "a.md", "component": "a", "role": "nope"}]), "role"),
        (spec_bytes([{"path": "a.md", "component": "A", "role": "spec"}]), "component"),
        (spec_bytes([{"path": "a.md", "component": "a", "role": "spec", "x": 1}]), "unknown keys"),
        (spec_bytes([{"path": "a.md", "component": "a", "role": "spec", "test_only": "y"}]),
         "boolean"),
    ],
)
def test_invalid_specs(raw: bytes, match: str) -> None:
    with pytest.raises(bc.ContractError, match=match):
        bc.load_spec(raw)


# ------------------------------------------------------------------------------------ verify
@pytest.fixture
def bundle(tmp_path: Path) -> bytes:
    data, _ = bc.build(mini_repo(tmp_path), bc.WorktreeSource(tmp_path))
    return data


def test_verify_detects_tampering(bundle: bytes) -> None:
    files = unzip(bundle)
    files["dentisai-contracts-1.2.3/triage/dist/rules.json"] = b'{"version": "evil"}\n'
    with pytest.raises(bc.ContractError, match="size/sha256 mismatch"):
        bc.verify(rezip(files))


def test_verify_detects_extra_and_missing_files(bundle: bytes) -> None:
    files = unzip(bundle)
    with pytest.raises(bc.ContractError, match="extra=.*evil.js"):
        bc.verify(rezip({**files, "dentisai-contracts-1.2.3/evil.js": b"x"}))
    files.pop("dentisai-contracts-1.2.3/triage/tests/cases.json")
    with pytest.raises(bc.ContractError, match="missing=.*cases.json"):
        bc.verify(rezip(files))


def test_verify_structure_errors(bundle: bytes) -> None:
    with pytest.raises(bc.ContractError, match="not a zip"):
        bc.verify(b"nope")
    with pytest.raises(bc.ContractError, match="sidecar"):
        bc.verify(bundle, expected_sha256="0" * 64)
    files = unzip(bundle)
    with pytest.raises(bc.ContractError, match="one top-level folder"):
        bc.verify(rezip({**files, "other/x": b"x"}))
    renamed = {k.replace("1.2.3", "9.9.9"): v for k, v in files.items()}
    with pytest.raises(bc.ContractError, match="folder name"):
        bc.verify(rezip(renamed))
    files.pop("dentisai-contracts-1.2.3/CONTRACTS.json")
    with pytest.raises(bc.ContractError, match="CONTRACTS.json missing"):
        bc.verify(rezip(files))


def test_verify_accepts_matching_sidecar(bundle: bytes) -> None:
    assert bc.verify(bundle, expected_sha256=sha(bundle))["bundle_version"] == "1.2.3"


# --------------------------------------------------------------------------------------- git
@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_git_source_uses_committed_bytes_not_the_working_copy(tmp_path: Path) -> None:
    spec = mini_repo(tmp_path)

    def git(*a: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)

    git("init", "-q")
    git("-c", "core.autocrlf=false", "add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "init")
    committed, index = bc.build(spec, bc.GitSource(tmp_path, "HEAD"))
    assert index["source"]["dirty"] is False and len(index["source"]["commit"]) == 40
    # CRLF / uncommitted edits in the working copy do not change a release build.
    write(tmp_path, "docs/style/forbidden_phrases.md", b"# phrases\r\nedited\r\n")
    again, _ = bc.build(spec, bc.GitSource(tmp_path, "HEAD"))
    assert again == committed
    with pytest.raises(bc.ContractError, match="git rev-parse failed"):
        bc.GitSource(tmp_path, "no-such-ref")
    with pytest.raises(bc.ContractError, match="invalid --ref"):
        bc.GitSource(tmp_path, "--upload-pack=x")


# --------------------------------------------------------------------------------------- cli
def test_cli_build_and_verify(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo, out = tmp_path / "repo", tmp_path / "out"
    mini_repo(repo)
    spec_file = tmp_path / "contracts.json"
    spec_file.write_bytes(spec_bytes([
        {"path": "triage/tests/cases.json", "component": "triage", "role": "parity"},
    ]))
    args = ["build", "--spec", str(spec_file), "--root", str(repo), "--out", str(out), "--worktree"]
    assert bc.main(args) == 0
    zip_path = out / "dentisai-contracts-1.2.3-dirty.zip"
    assert (out / "CONTRACTS.json").is_file()
    digest, name = (out / "dentisai-contracts-1.2.3-dirty.zip.sha256").read_text().split()
    assert digest == sha(zip_path.read_bytes()) and name == zip_path.name
    assert bc.main(["verify", str(zip_path)]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["sidecar_checked"] is True

    assert bc.main([*args, "--expect-version", "2.0.0"]) == 1
    assert "tag/version mismatch" in capsys.readouterr().err
    zip_path.write_bytes(b"corrupt")
    assert bc.main(["verify", str(zip_path)]) == 1
