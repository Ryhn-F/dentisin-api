"""Forbidden-phrase parser/matcher and source-file resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from triage import paths
from triage.forbidden import (
    find_forbidden,
    iter_strings,
    lint_document,
    load_forbidden,
    parse_forbidden_markdown,
)

SAMPLE = """# Frasa terlarang
| Terlarang | Alasan | Gunakan |
|---|---|---|
| "Anda menderita …", "aman" | x | "indikasi" |
| "akurasi XX%" (di UI) | y | — |
| tanpa kutip | z | — |
| "…" | hanya elipsis, diabaikan | — |

Wajib hadir: "Hasil ini bukan diagnosis dan tidak menggantikan pemeriksaan dokter gigi."
| "diagnosis" | a | b |
"""


def test_parse_sample() -> None:
    parsed = parse_forbidden_markdown(SAMPLE)
    assert parsed.phrases == ("Anda menderita", "aman", "akurasi XX%", "diagnosis")
    assert parsed.mandatory == (
        "Hasil ini bukan diagnosis dan tidak menggantikan pemeriksaan dokter gigi.",
    )


@pytest.mark.parametrize(
    ("text", "hits"),
    [
        ("Anak Anda  MENDERITA karies", ["Anda menderita"]),
        ("Ini aman.", ["aman"]),
        ("Keamanan data dan rasa nyaman", []),  # whole words only
        ("akurasi 92% di toko", ["akurasi XX%"]),
        ("akurasi model", []),
        ("Hasil ini bukan diagnosis dan tidak menggantikan pemeriksaan dokter gigi.", []),
        ("Ini diagnosis awal.", ["diagnosis"]),
    ],
)
def test_matching(text: str, hits: list[str]) -> None:
    assert find_forbidden(text, parse_forbidden_markdown(SAMPLE)) == hits


def test_real_list_parses_and_shared_equals_vendored() -> None:
    real = load_forbidden()
    assert "100%" in real.phrases
    assert "bebas karies" in real.phrases
    assert find_forbidden("Hasilnya 100% akurat", real) == ["100%"]
    assert find_forbidden("Anak bebas karies!", real) == ["bebas karies"]
    if paths.SHARED_FORBIDDEN_PHRASES_PATH.is_file():
        assert load_forbidden(paths.VENDORED_FORBIDDEN_PHRASES_PATH) == real, (
            "vendored backend/docs/style/forbidden_phrases.md drifted from the shared original"
        )


def test_iter_strings_and_lint_document() -> None:
    doc = {"a": ["Ini aman", 3, {"b": "oke"}], "c": None}
    assert list(iter_strings(doc)) == [("$.a[0]", "Ini aman"), ("$.a[2].b", "oke")]
    assert lint_document(doc, parse_forbidden_markdown(SAMPLE)) == ["$.a[0]: 'aman'"]


def test_path_resolution_order(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    custom = tmp_path / "custom.md"
    custom.write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setenv(paths.FORBIDDEN_PHRASES_ENV, str(custom))
    assert paths.forbidden_phrases_path() == custom
    assert load_forbidden().phrases[0] == "Anda menderita"

    monkeypatch.delenv(paths.FORBIDDEN_PHRASES_ENV)
    shared = tmp_path / "shared.md"
    monkeypatch.setattr(paths, "SHARED_FORBIDDEN_PHRASES_PATH", shared)
    assert paths.forbidden_phrases_path() == paths.VENDORED_FORBIDDEN_PHRASES_PATH
    shared.write_text(SAMPLE, encoding="utf-8")
    assert paths.forbidden_phrases_path() == shared
