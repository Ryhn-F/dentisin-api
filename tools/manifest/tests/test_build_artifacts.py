"""build_manifest (artifact collection) and artifact verification (local + https)."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from conftest import BASE_URL, Artifacts, KeyMaterial, stage, write_json
from dentisai_manifest.errors import ErrorCode, ManifestError
from dentisai_manifest.placeholders import scan_text
from dentisai_manifest.schema import validate_manifest
from dentisai_manifest.signing import build_manifest, parse_sha256_file
from dentisai_manifest.verify import (
    check_local,
    check_remote,
    common_base_url,
    fetch_capped,
    first_failure,
    read_binary,
)


def refused(art: Artifacts, **over: object) -> str:
    with pytest.raises(ManifestError) as ei:
        build_manifest(art.inputs(**over))
    assert ei.value.code in (ErrorCode.E_REFUSED, ErrorCode.E_USAGE)
    return ei.value.message


# --- build ---------------------------------------------------------------------------


def test_build_happy_path(artifacts: Artifacts) -> None:
    b = build_manifest(artifacts.inputs())
    assert b.findings == []
    assert b.warnings == []
    m = b.manifest
    model = m["model"]
    assert isinstance(model, dict)
    assert model["version"] == "0.3.1"
    assert model["url"] == f"{BASE_URL}/model/0.3.1/model.tflite"
    assert model["size_bytes"] == (artifacts.model_dir / "model.tflite").stat().st_size
    assert [rel for _, rel in b.files] == [
        "model/0.3.1/model.tflite",
        "model/0.3.1/labels.json",
        "rules/2026.10.1/rules.json",
        "recommender/1.0.0/recommender.json",
    ]
    m["signer_key_id"] = "0" * 16
    validate_manifest(m)


def test_build_refuses_missing_sha256_and_labels(artifacts: Artifacts) -> None:
    (artifacts.model_dir / "SHA256").unlink()
    assert "SHA256" in refused(artifacts)


def test_build_refuses_missing_labels(artifacts: Artifacts) -> None:
    (artifacts.model_dir / "labels.json").unlink()
    assert "labels.json" in refused(artifacts)


def test_build_refuses_sha_mismatch(artifacts: Artifacts) -> None:
    (artifacts.model_dir / "model.tflite").write_bytes(b"changed")
    assert "does not match model.tflite" in refused(artifacts)


def test_build_refuses_labels_sha_mismatch(artifacts: Artifacts) -> None:
    write_json(artifacts.model_dir / "labels.json", {"license": "Apache-2.0", "x": 2})
    assert "does not match labels.json" in refused(artifacts)


def test_sha256_file_formats() -> None:
    h = "a" * 64
    assert parse_sha256_file(f"# comment\n{h}\n") == {"model.tflite": h}
    assert parse_sha256_file(f"{h.upper()}  ./out/model.tflite\n")["model.tflite"] == h
    with pytest.raises(ManifestError, match="unparseable"):
        parse_sha256_file("nonsense")
    with pytest.raises(ManifestError, match="no entry"):
        parse_sha256_file(f"{h}  labels.json")


def test_build_misc_refusals(artifacts: Artifacts, tmp_path: Path) -> None:
    assert "base URL" in refused(artifacts, base_url="http://abc.supabase.co/x")
    bad_dir = tmp_path / "bad version!"
    artifacts.model_dir.rename(bad_dir)
    assert "not a valid version" in refused(artifacts, model_dir=bad_dir)


def test_build_license_sources(artifacts: Artifacts) -> None:
    write_json(artifacts.model_dir / "labels.json", {"license": {"code": "Apache-2.0"}})
    (artifacts.model_dir / "SHA256").write_text(
        parse_and_rehash(artifacts.model_dir), encoding="utf-8"
    )
    assert "license unknown" in refused(artifacts)
    b = build_manifest(artifacts.inputs(model_license="Apache-2.0 AND CC-BY-4.0"))
    model = b.manifest["model"]
    assert isinstance(model, dict)
    assert model["license"] == "Apache-2.0 AND CC-BY-4.0"
    b2 = build_manifest(artifacts.inputs(model_license="LicenseRef-PLACEHOLDER"))
    assert any(f.file == "--model-license" for f in b2.findings)


def parse_and_rehash(model_dir: Path) -> str:
    import hashlib

    h = hashlib.sha256((model_dir / "model.tflite").read_bytes()).hexdigest()
    return f"{h}  model.tflite\n"


def test_build_versions(artifacts: Artifacts) -> None:
    write_json(artifacts.rules, {"rules": []})
    assert "no version" in refused(artifacts)
    b = build_manifest(artifacts.inputs(rules_version="2026.11.0", recommender_version="2.0.0"))
    rules = b.manifest["rules"]
    assert isinstance(rules, dict)
    assert rules["version"] == "2026.11.0"
    assert "invalid characters" in refused(artifacts, rules_version="../x")


def test_build_invalid_json(artifacts: Artifacts) -> None:
    artifacts.rules.write_text("{not json", encoding="utf-8")
    assert "not valid UTF-8 JSON" in refused(artifacts)


def test_build_missing_recommender_file(artifacts: Artifacts) -> None:
    artifacts.recommender.unlink()
    assert "recommender file missing" in refused(artifacts)


def test_placeholder_findings(artifacts: Artifacts) -> None:
    write_json(
        artifacts.rules,
        {"version": "1.0.0", "rules": [{"id": "R1", "params": {"x": None}, "note": "PLACEHOLDER"}]},
    )
    write_json(
        artifacts.recommender,
        {"version": "1.0.0", "products": [{"verified": False}, {"status": "needs_review"}]},
    )
    (artifacts.model_dir / "MODEL_CARD.md").unlink()
    b = build_manifest(artifacts.inputs())
    reasons = sorted(f.reason for f in b.findings)
    assert reasons == ['"status": "needs_review"', '"verified": false', "contains 'PLACEHOLDER' x1"]
    assert any("MODEL_CARD.md missing" in w for w in b.warnings)
    assert str(b.findings[0]).startswith(str(artifacts.rules))


def test_license_verified_false_in_labels(artifacts: Artifacts) -> None:
    write_json(artifacts.model_dir / "labels.json", {"license": "MIT", "license_verified": False})
    (artifacts.model_dir / "SHA256").write_text(
        parse_and_rehash(artifacts.model_dir), encoding="utf-8"
    )
    (artifacts.model_dir / "MODEL_CARD.md").write_text("weights: PLACEHOLDER", encoding="utf-8")
    b = build_manifest(artifacts.inputs())
    assert {f.reason for f in b.findings} == {
        '"license_verified": false',
        "contains 'PLACEHOLDER' x1",
    }


def test_scan_text_invalid_json() -> None:
    assert scan_text("{", "f.json", is_json=True)[0].reason == "is not valid JSON"


# --- local artifact verification -----------------------------------------------------


def test_check_local_ok_and_failures(
    tmp_path: Path, key: KeyMaterial, artifacts: Artifacts
) -> None:
    out = tmp_path / "out"
    s = stage(out, artifacts, key)
    res = check_local(s.manifest, out)
    assert all(r.ok for r in res)
    assert first_failure(res) is None
    assert common_base_url(s.manifest.artifacts()) == BASE_URL + "/"
    assert all(r.ok for r in check_local(s.manifest, out, BASE_URL))

    # swapped content: same size, different bytes -> hash mismatch
    model = out / "model/0.3.1/model.tflite"
    data = bytearray(model.read_bytes())
    data[0] ^= 0xFF
    model.write_bytes(bytes(data))
    # bigger file -> size mismatch (reported as size+)
    (out / "rules/2026.10.1/rules.json").write_bytes(b"x" * 100_000)
    # smaller file -> size mismatch
    (out / "recommender/1.0.0/recommender.json").write_bytes(b"{}")
    # missing file
    (out / "model/0.3.1/labels.json").unlink()
    by_name = {r.name: r for r in check_local(s.manifest, out)}
    assert by_name["model"].code == ErrorCode.E_ARTIFACT_HASH
    assert by_name["model.labels"].code == ErrorCode.E_ARTIFACT_MISSING
    assert by_name["rules"].code == ErrorCode.E_ARTIFACT_SIZE
    assert by_name["rules"].message is not None and "+" in by_name["rules"].message
    assert by_name["rules"].actual_size == by_name["rules"].expected_size + 1
    assert by_name["recommender"].code == ErrorCode.E_ARTIFACT_SIZE
    assert first_failure(list(by_name.values())) is by_name["model"]
    json.dumps(by_name["model"].to_json())

    other = check_local(s.manifest, out, "https://elsewhere.example/")
    assert all(r.code == ErrorCode.E_ARTIFACT_MISSING for r in other)
    assert check_local(s.manifest, out, BASE_URL)[2].code == ErrorCode.E_ARTIFACT_SIZE


# --- remote artifact verification ----------------------------------------------------


class FakeResp:
    def __init__(
        self,
        body: bytes,
        url: str,
        length: str | None = None,
        fail_after: int | None = None,
    ) -> None:
        self._buf = io.BytesIO(body)
        self._url = url
        self._len = length
        self._fail_after = fail_after
        self.closed = False

    def read(self, n: int = -1, /) -> bytes:
        if self._fail_after is not None and self._buf.tell() >= self._fail_after:
            raise TimeoutError("read timed out")
        return self._buf.read(n)

    def geturl(self) -> str:
        return self._url

    def getheader(self, name: str, default: str | None = None, /) -> str | None:
        return self._len if name == "Content-Length" else default

    def close(self) -> None:
        self.closed = True


def test_check_remote(tmp_path: Path, key: KeyMaterial, artifacts: Artifacts) -> None:
    out = tmp_path / "out"
    s = stage(out, artifacts, key)
    files = {ref.url: out / ref.url[len(BASE_URL) + 1 :] for ref in s.manifest.artifacts()}
    seen: list[tuple[str, float]] = []

    def opener(req: urllib.request.Request, timeout: float) -> FakeResp:
        seen.append((req.full_url, timeout))
        assert req.get_header("User-agent", "").startswith("dentisai-manifest")
        return FakeResp(files[req.full_url].read_bytes(), req.full_url)

    assert all(r.ok for r in check_remote(s.manifest, 5.0, opener))
    assert {t for _, t in seen} == {5.0}

    urls = [r.url for r in s.manifest.artifacts()]

    def bad_opener(req: urllib.request.Request, timeout: float) -> FakeResp:
        url = req.full_url
        body = files[url].read_bytes()
        if url == urls[0]:
            return FakeResp(body[:-1] + b"!", url)  # hash mismatch
        if url == urls[1]:
            return FakeResp(body, url, length=str(10**9))  # declared too large
        if url == urls[2]:
            return FakeResp(body, "http://downgraded.example/x")  # redirect to http
        return FakeResp(body, url, fail_after=0)  # read timeout

    res = {r.name: r for r in check_remote(s.manifest, 1.0, bad_opener)}
    assert res["model"].code == ErrorCode.E_ARTIFACT_HASH
    assert res["model.labels"].code == ErrorCode.E_ARTIFACT_SIZE
    assert res["rules"].code == ErrorCode.E_ARTIFACT_FETCH
    assert res["recommender"].code == ErrorCode.E_ARTIFACT_FETCH

    def unreachable(req: urllib.request.Request, timeout: float) -> FakeResp:
        raise urllib.error.URLError("no route")

    assert {r.code for r in check_remote(s.manifest, 1.0, unreachable)} == {
        ErrorCode.E_ARTIFACT_FETCH
    }


def test_fetch_capped() -> None:
    url = "https://abc.supabase.co/m/manifest.json"

    def ok(req: urllib.request.Request, timeout: float) -> FakeResp:
        return FakeResp(b"hello", req.full_url)

    assert fetch_capped(url, 5, 1.0, ok) == b"hello"

    def code(fn_url: str, cap: int, opener: object) -> ErrorCode:
        with pytest.raises(ManifestError) as ei:
            fetch_capped(fn_url, cap, 1.0, opener)  # type: ignore[arg-type]
        return ei.value.code

    assert code(url, 4, ok) is ErrorCode.E_TOO_LARGE
    assert code("http://abc.supabase.co/x", 5, ok) is ErrorCode.E_ARTIFACT_FETCH

    def redirect(req: urllib.request.Request, timeout: float) -> FakeResp:
        return FakeResp(b"x", "http://evil.example/")

    assert code(url, 5, redirect) is ErrorCode.E_ARTIFACT_FETCH

    def refuse(req: urllib.request.Request, timeout: float) -> FakeResp:
        raise TimeoutError("timed out")

    assert code(url, 5, refuse) is ErrorCode.E_ARTIFACT_FETCH

    def slow(req: urllib.request.Request, timeout: float) -> FakeResp:
        return FakeResp(b"abc", req.full_url, fail_after=1)

    assert code(url, 5, slow) is ErrorCode.E_ARTIFACT_FETCH


def test_default_opener_is_urllib(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[float] = []

    def fake_urlopen(req: urllib.request.Request, timeout: float) -> FakeResp:
        calls.append(timeout)
        return FakeResp(b"ok", req.full_url)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert fetch_capped("https://abc.supabase.co/x", 10, 2.5) == b"ok"
    assert calls == [2.5]


def test_read_binary_cap() -> None:
    assert read_binary(io.BytesIO(b"abc"), 3) == b"abc"
    with pytest.raises(ManifestError):
        read_binary(io.BytesIO(b"abcd"), 3)


def test_license_from_ml_export_layout(artifacts: Artifacts) -> None:
    from dentisai_manifest.signing import license_from_labels

    labels: Any = {
        "model": {"version": "0.3.1"},
        "licenses": {
            "code": {"license": "Apache-2.0", "verified": True},
            "weights": {"license": "Apache-2.0", "verified": True},
            "datasets": [{"id": "ahmed2025", "license": "CC-BY-4.0"}],
        },
    }
    assert license_from_labels(labels) == "Apache-2.0 AND CC-BY-4.0"
    assert license_from_labels([]) is None
    assert license_from_labels({"licenses": "MIT"}) is None
    assert license_from_labels({"licenses": {"code": {"license": None}}}) is None
    assert license_from_labels({"licenses": {"code": "x", "weights": {}, "datasets": [1]}}) is None
    write_json(artifacts.model_dir / "labels.json", labels)
    (artifacts.model_dir / "SHA256").write_text(
        parse_and_rehash(artifacts.model_dir), encoding="utf-8"
    )
    model = build_manifest(artifacts.inputs()).manifest["model"]
    assert isinstance(model, dict)
    assert model["license"] == "Apache-2.0 AND CC-BY-4.0"
    labels["model"] = {"version": "9.9.9"}
    write_json(artifacts.model_dir / "labels.json", labels)
    assert "model.version" in refused(artifacts)


def test_sibling_sha256sums(artifacts: Artifacts) -> None:
    import hashlib

    sums = artifacts.rules.parent / "SHA256SUMS"
    good = hashlib.sha256(artifacts.rules.read_bytes()).hexdigest()
    sums.write_text(f"{good}  rules.json\n{'0' * 64}  other.json\n", encoding="utf-8")
    build_manifest(artifacts.inputs())
    sums.write_text(f"{'0' * 64}  rules.json\n", encoding="utf-8")
    assert "stale dist" in refused(artifacts)


def test_triage_style_markers(artifacts: Artifacts) -> None:
    write_json(
        artifacts.rules,
        {
            "version": "0.1.0-draft",
            "status": "DRAFT",
            "texts_review": {"reviewed_by": "PENDING_DRG"},
            "params": {"x": {"value": None, "status": "PROPOSED"}},
        },
    )
    reasons = sorted(f.reason for f in build_manifest(artifacts.inputs()).findings)
    assert reasons == ['"status": "DRAFT"', '"status": "PROPOSED"', "contains 'PENDING_DRG' x1"]


def test_resolve_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dentisai_manifest.keys import BACKEND_ROOT
    from dentisai_manifest.signing import (
        DEFAULT_RECOMMENDER_FILE,
        DEFAULT_RULES_FILE,
        resolve_input,
    )

    assert DEFAULT_RULES_FILE == BACKEND_ROOT / "triage" / "dist" / "rules.json"
    assert DEFAULT_RECOMMENDER_FILE == BACKEND_ROOT / "recommender" / "dist" / "recommender.json"
    monkeypatch.chdir(tmp_path)
    (tmp_path / "here.json").write_text("{}")
    assert resolve_input(Path("here.json")) == Path("here.json")
    assert resolve_input(Path("ml/exports/1.0.0")) == BACKEND_ROOT / "ml/exports/1.0.0"
    assert resolve_input(tmp_path / "abs") == tmp_path / "abs"


def test_labels_must_be_object(artifacts: Artifacts) -> None:
    write_json(artifacts.model_dir / "labels.json", ["not", "an", "object"])
    (artifacts.model_dir / "SHA256").write_text(
        parse_and_rehash(artifacts.model_dir), encoding="utf-8"
    )
    assert "must be a JSON object" in refused(artifacts)
