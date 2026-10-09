from __future__ import annotations

import json
from pathlib import Path

import pytest

import lint_forbidden_phrases as lint
from opscommon import backend_root

PHRASES = lint.resolve_phrases_path(backend_root(), {})


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(lint.PHRASES_ENV, raising=False)


@pytest.fixture(scope="module")
def rules() -> list[lint.Rule]:
    parsed, _ = lint.load_rules(PHRASES)
    return parsed


def hit_ids(text: str, rules: list[lint.Rule]) -> list[str]:
    return [h.rule_id for h in lint.scan_text(text, rules)]


# --- parsing -----------------------------------------------------------------------------


def test_parses_all_quoted_phrases_from_the_real_table(rules: list[lint.Rule]) -> None:
    phrases = {p.text for r in rules for p in r.phrases}
    assert {
        "Anda menderita …",
        "anak Anda menderita …",
        "diagnosis",
        "didiagnosis",
        "terdiagnosis",
        "pasti",
        "dipastikan",
        "100%",
        "positif karies",
        "negatif karies",
        "tidak perlu ke dokter",
        "aman",
        "sehat sempurna",
        "bebas karies",
        "obati sendiri",
        "akurasi XX%",
    } <= phrases


def test_unmechanized_rules_are_reported() -> None:
    _, skipped = lint.load_rules(PHRASES)
    texts = " ".join(u.text for u in skipped).lower()
    assert "dosis obat untuk anak" in texts
    assert "nama merek" in texts
    assert "wajib hadir" in texts


def test_parse_synthetic_table() -> None:
    md = (
        "# t\n\n| Terlarang | Alasan | Gunakan |\n|---|---|---|\n"
        '| "alfa beta …", "gamma" | r1 | u1 |\n'
        '| "delta" tanpa disclaimer | r2 | u2 |\n'
        "| tanpa tanda kutip | r3 | u3 |\n\n"
        '| Lain | tabel |\n|---|---|\n| "abaikan" | x |\n'
    )
    rules, skipped = lint.parse_rules(md)
    assert [r.rule_id for r in rules] == ["FP01", "FP02"]
    assert rules[1].needs_disclaimer
    assert [p.text for p in rules[0].phrases] == ["alfa beta …", "gamma"]
    assert [u.where for u in skipped] == ["FP03"]


def test_no_rules_is_a_config_error(tmp_path: Path) -> None:
    f = tmp_path / "p.md"
    f.write_text("tidak ada tabel", encoding="utf-8")
    with pytest.raises(lint.LintConfigError):
        lint.load_rules(f)
    with pytest.raises(lint.LintConfigError):
        lint.load_rules(tmp_path / "missing.md")


# --- true positives ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("Anda menderita karies gigi", "FP01"),
        ("Mungkin anak Anda menderita gigi berlubang", "FP01"),
        ("Hasil diagnosis menunjukkan lubang", "FP02"),
        ("Anak didiagnosis karies", "FP02"),
        ("sudah terdiagnosis", "FP02"),
        ("Ini pasti karies", "FP03"),
        ("Karies dipastikan ada", "FP03"),
        ("Akurasi 100%", "FP03"),
        ("tingkat 100 % benar", "FP03"),
        ("Hasil: positif karies", "FP04"),
        ("negatif karies", "FP04"),
        ("Kamu tidak perlu ke dokter gigi", "FP05"),
        ("Gigimu aman.", "FP05"),
        ("Giginya AMAN", "FP05"),
        ("Giginya amannya terjamin", "FP05"),
        ("Kondisi sehat sempurna", "FP05"),
        ("Gigimu bebas karies!", "FP06"),
        ("Silakan obati sendiri di rumah", "FP07"),
        ("Akurasi 92% menurut uji", "FP08"),
        ("akurasi 92,5 %", "FP08"),
        ("PASTI", "FP03"),
    ],
)
def test_forbidden_phrases_are_flagged(text: str, rule: str, rules: list[lint.Rule]) -> None:
    assert rule in hit_ids(text, rules)


# --- false-positive guards ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Ini soal keamanan data",
        "Tolong amankan sikat gigimu",
        "mengamankan data",
        "sudah diamankan",
        "pengaman",
        "Pastikan menyikat dua kali sehari",
        "memastikan jadwal kontrol",
        "kepastian jadwal",
        "pasta gigi berfluoride",
        "pastel",
        "Total 1100% kenaikan",
        "100 gigi",
        "Akurasi model sedang dievaluasi",
        "positif sekali semangatnya",
        "Hasil ini bukan diagnosis dan tidak menggantikan pemeriksaan dokter gigi.",
        "Ini bukan suatu diagnosis",
        "bukan hasil diagnosis",
        "Hasilnya belum pasti",
        "Hasil tidak pasti",
        "Karies belum dapat dipastikan dari foto",
        "tidak dapat dipastikan",
        "Lingkungan tidak aman untuk anak",
        "kurang aman",
        "Anda tidak menderita",
        "",
    ],
)
def test_safe_text_is_not_flagged(text: str, rules: list[lint.Rule]) -> None:
    assert hit_ids(text, rules) == []


def test_bebas_karies_allowed_with_nearby_disclaimer(rules: list[lint.Rule]) -> None:
    ok = "Belum terlihat lubang, bebas karies pada foto ini.\nHasil ini bukan diagnosis."
    assert hit_ids(ok, rules) == []
    far = "bebas karies\n\n\n\nHasil ini bukan diagnosis."
    assert "FP06" in hit_ids(far, rules)
    assert "FP06" in hit_ids("bebas karies", rules)


def test_disclaimer_does_not_unlock_other_rules(rules: list[lint.Rule]) -> None:
    text = "Ini pasti karies. Hasil ini bukan diagnosis."
    assert hit_ids(text, rules) == ["FP03"]


def test_exemption_does_not_hide_a_second_hit_on_same_line(rules: list[lint.Rule]) -> None:
    ids = hit_ids("belum pasti, tetapi aman dan pasti", rules)
    assert ids.count("FP03") == 1
    assert "FP05" in ids


def test_column_and_line_numbers(rules: list[lint.Rule]) -> None:
    hits = lint.scan_text("ok\nkata aman di sini\n", rules, "x.txt")
    assert [(h.path, h.line, h.col, h.matched) for h in hits] == [("x.txt", 2, 6, "aman")]


def test_nbsp_and_case_and_extra_spaces(rules: list[lint.Rule]) -> None:
    assert "FP04" in hit_ids("POSITIF  KARIES", rules)


def test_ignore_pragma_suppresses_line(rules: list[lint.Rule]) -> None:
    assert hit_ids("# aman  forbidden-phrases:ignore", rules) == []


# --- scanning files ----------------------------------------------------------------------


def make_repo(tmp_path: Path) -> Path:
    (tmp_path / "docs/style").mkdir(parents=True)
    (tmp_path / "docs/style/forbidden_phrases.md").write_text(
        PHRASES.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "CLAUDE.md").write_text("x", encoding="utf-8")
    return tmp_path


def test_cli_clean_default_paths_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_repo(tmp_path)
    code = lint.main(["--root", str(root)])
    err = capsys.readouterr().err
    assert code == 0
    assert "default path not present yet" in err
    assert "NOT MECHANIZED" in err


def test_cli_reports_file_line_and_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_repo(tmp_path)
    (root / "triage").mkdir()
    (root / "triage/rules.yaml").write_text(
        "a: Aman dan nyaman\nb: ok\nreason: 'Anda menderita karies'\n", encoding="utf-8"
    )
    (root / "triage/skip.py").write_text("# aman", encoding="utf-8")
    code = lint.main(["--root", str(root)])
    out = capsys.readouterr().out
    assert code == 1
    assert "triage/rules.yaml:1:4: FP05" in out
    assert "triage/rules.yaml:3:" in out
    assert "skip.py" not in out


def test_docs_dir_excluded_unless_included(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    (root / "triage/docs").mkdir(parents=True)
    (root / "triage/docs/notes.txt").write_text("aman", encoding="utf-8")
    assert lint.main(["--root", str(root)]) == 0
    assert lint.main(["--root", str(root), "--include-docs"]) == 1


def test_explicit_file_always_scanned_even_markdown(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    f = root / "note.md"
    f.write_text("pasti", encoding="utf-8")
    assert lint.main(["--root", str(root), str(f)]) == 1


def test_explicit_missing_path_is_error(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    assert lint.main(["--root", str(root), str(root / "nope")]) == 2


def test_missing_phrase_file_is_error(tmp_path: Path) -> None:
    assert lint.main(["--root", str(tmp_path)]) == 2


def test_extension_filter(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    (root / "recommender").mkdir()
    (root / "recommender/a.md").write_text("aman", encoding="utf-8")
    (root / "recommender/b.ts").write_text("aman", encoding="utf-8")
    assert lint.main(["--root", str(root)]) == 0
    assert lint.main(["--root", str(root), "--ext", "ts"]) == 1


def test_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = make_repo(tmp_path)
    (root / "triage").mkdir()
    (root / "triage/c.json").write_text(json.dumps({"t": "dipastikan"}), encoding="utf-8")
    code = lint.main(["--root", str(root), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert code == 1
    assert data["ok"] is False
    assert data["files_scanned"] == 1
    assert data["hits"][0]["rule_id"] == "FP03"
    assert data["hits"][0]["path"] == "triage/c.json"
    assert any("Nama merek" in u["text"] for u in data["unmechanized"])


def test_list_rules(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = make_repo(tmp_path)
    assert lint.main(["--root", str(root), "--list-rules"]) == 0
    out = capsys.readouterr().out
    assert "FP06 [needs disclaimer]" in out


def test_binary_file_is_skipped_not_crash(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    (root / "triage").mkdir()
    (root / "triage/x.json").write_bytes(b"\xff\xfe\x00\x80\x81")
    assert lint.main(["--root", str(root)]) == 0


def test_real_sample_tts_script_is_clean() -> None:
    sample = backend_root() / "tools/tts/script.sample.csv"
    assert lint.main([str(sample)]) == 0


# --- phrase source resolution and sync check ---------------------------------------------


def make_monorepo(tmp_path: Path, shared_text: str | None, vendored_text: str | None) -> Path:
    backend = tmp_path / "backend"
    backend.mkdir()
    for base, text in ((tmp_path, shared_text), (backend, vendored_text)):
        if text is not None:
            (base / "docs/style").mkdir(parents=True)
            (base / "docs/style/forbidden_phrases.md").write_text(text, encoding="utf-8")
    return backend


def test_resolution_prefers_env_then_shared_then_vendored(tmp_path: Path) -> None:
    real = PHRASES.read_text(encoding="utf-8")
    backend = make_monorepo(tmp_path, real, real)
    shared = tmp_path / lint.DEFAULT_PHRASES_FILE
    vendored = backend / lint.DEFAULT_PHRASES_FILE
    assert lint.resolve_phrases_path(backend, {}) == shared
    custom = tmp_path / "custom.md"
    assert lint.resolve_phrases_path(backend, {lint.PHRASES_ENV: str(custom)}) == custom
    shared.unlink()
    assert lint.resolve_phrases_path(backend, {}) == vendored


def test_differing_copies_fail_with_exit_3(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    real = PHRASES.read_text(encoding="utf-8")
    backend = make_monorepo(tmp_path, real, real + "\n| extra | x | y |\n")
    assert lint.check_sync(backend) is not None
    assert lint.main(["--root", str(backend)]) == 3
    assert lint.main(["--root", str(backend), "--check-sync"]) == 3
    assert "differs" in capsys.readouterr().err


def test_identical_copies_ignoring_line_endings_are_in_sync(tmp_path: Path) -> None:
    real = PHRASES.read_text(encoding="utf-8")
    backend = make_monorepo(tmp_path, real, real)
    vendored = backend / lint.DEFAULT_PHRASES_FILE
    vendored.write_bytes(real.replace("\n", "\r\n").encode("utf-8"))
    assert lint.check_sync(backend) is None
    assert lint.main(["--root", str(backend), "--check-sync"]) == 0


def test_only_vendored_or_only_shared_is_fine(tmp_path: Path) -> None:
    real = PHRASES.read_text(encoding="utf-8")
    (tmp_path / "a").mkdir()
    only_vendored = make_monorepo(tmp_path / "a", None, real)
    assert lint.check_sync(only_vendored) is None
    assert lint.main(["--root", str(only_vendored)]) == 0


def test_env_override_is_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom = tmp_path / "p.md"
    custom.write_text(
        '| Terlarang | Alasan | Gunakan |\n|---|---|---|\n| "zzz" | r | u |\n', encoding="utf-8"
    )
    target = tmp_path / "t.txt"
    target.write_text("ada zzz di sini", encoding="utf-8")
    monkeypatch.setenv(lint.PHRASES_ENV, str(custom))
    assert lint.main(["--root", str(tmp_path / "backend"), str(target)]) == 1


def test_backend_default_paths_have_no_docs() -> None:
    assert not any(p.startswith("docs") for p in lint.DEFAULT_SCAN_PATHS)
