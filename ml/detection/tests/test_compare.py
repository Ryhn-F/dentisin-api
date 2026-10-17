from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from bake_off import compare as cmp


def fake_report(sens: float | None = 0.8, split_sha: str | None = "s" * 64, with_ci: bool = True, warnings: list[str] | None = None) -> dict[str, Any]:
    ci = {"lo": 0.7, "hi": 0.9, "n_valid": 1000} if with_ci else None
    lvl = {"sensitivity": sens, "specificity": 0.9, "ci95": {"sensitivity": ci, "specificity": ci} if with_ci else None}
    return {
        "inputs": {"split": "test", "split_sha256": split_sha, "gt_sha256": "g" * 64},
        "settings": {"score_threshold_status": "PLACEHOLDER"},
        "counts": {"bootstrap_unit": "patient"},
        "overall": {
            "map50": 0.42, "map50_ci95": ci,
            "per_class": {
                "caries_permanent": {"image_level": lvl, "box_level": {"sensitivity": 0.6, "ci95": None}, "ap50": 0.55},
                "caries_primary": {"image_level": {"sensitivity": None, "ci95": None}, "ap50": None},
                "any_caries": {"image_level": lvl, "ap50": 0.5},
            },
        },
        "warnings": warnings or [],
    }


META = {
    "yolox_nano": {"int8_size_mb": 3.9, "latency_ms": {"ram_3gb": 900, "ram_4gb": 700}, "export_difficulty": "medium",
                   "export_notes": "decoder outside graph", "weight_license_clarity": "unclear"},
    "mediapipe_modelmaker_mobilenetv2_ssd": {"int8_size_mb": 16.0, "latency_ms": {"ram_3gb": 400, "ram_4gb": 300}},
}


def test_render_table() -> None:
    md = cmp.render_markdown({"yolox_nano": fake_report(), "mediapipe_modelmaker_mobilenetv2_ssd": fake_report(0.5, with_ci=False),
                              "efficientdet_lite0": fake_report(warnings=["patient_id missing"])}, META)
    assert "bukan validasi klinis" in md and f"`{'s' * 64}`" in md
    rows = {ln.split("|")[1].strip(): ln for ln in md.splitlines() if ln.startswith("| `")}
    assert "0.80 (0.70–0.90)" in rows["`yolox_nano`"] and "lolos" in rows["`yolox_nano`"]
    assert "gagal" in rows["`mediapipe_modelmaker_mobilenetv2_ssd`"]  # 16 MB > 15
    assert "belum diukur" in rows["`efficientdet_lite0`"] and "belum dinilai" in rows["`efficientdet_lite0`"]
    assert "| — |" in rows["`yolox_nano`"]  # primary-tooth sensitivity undefined (no positives)
    assert "| 0.55 | — |" in rows["`yolox_nano`"]  # AP50 permanent | AP50 primary
    assert "decoder outside graph" in md and "patient_id missing" in md


def test_float_bundle_meta_uses_model_size_and_labels_the_precision() -> None:
    """A float16/float32 bundle has no int8_size_mb: the size gate uses model_size_mb, parity is n/a, the row names the precision."""
    meta = {"yolox_nano": {"int8_size_mb": None, "model_size_mb": 3.7, "precision": "float32", "parity_passed": None,
                           "latency_ms": {"ram_3gb": 900, "ram_4gb": 700}}}
    md = cmp.render_markdown({"yolox_nano": fake_report()}, meta)
    row = next(ln for ln in md.splitlines() if ln.startswith("| `yolox_nano`"))
    assert "`yolox_nano` · float32" in row and "| 3.7 |" in row and "n/a (float)" in row and "lolos" in row
    assert cmp.model_size_mb({}) is None and cmp.model_size_mb({"int8_size_mb": 2, "model_size_mb": 9}) == 2.0


def test_main_prints_utf8_even_on_a_cp1252_console(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rep = tmp_path / "r.json"
    rep.write_text(json.dumps(fake_report()), encoding="utf-8")
    assert cmp.main(["--report", f"a={rep}"]) == 0
    assert "≤" in capsys.readouterr().out


def test_refuses_mixed_splits() -> None:
    with pytest.raises(cmp.CompareError, match="different data"):
        cmp.render_markdown({"a": fake_report(), "b": fake_report(split_sha="t" * 64)}, {})
    with pytest.raises(cmp.CompareError, match="no split_sha256"):
        cmp.render_markdown({"a": fake_report(split_sha=None)}, {})


def test_gate_cell() -> None:
    assert cmp.gate_cell({}) == "belum diukur"
    assert cmp.gate_cell({"int8_size_mb": 5, "latency_ms": {"ram_3gb": 1600, "ram_4gb": 100}}) == "gagal"
    assert cmp.fmt_ci({"lo": None, "hi": None}) == ""


def test_parse_args_errors() -> None:
    with pytest.raises(cmp.CompareError, match="candidate=path"):
        cmp.parse_report_args(["nopath"])
    with pytest.raises(cmp.CompareError, match="duplicate"):
        cmp.parse_report_args(["a=x", "a=y"])


def test_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    r = tmp_path / "r.json"
    r.write_text(json.dumps(fake_report()), encoding="utf-8")
    m = tmp_path / "m.yaml"
    m.write_text(yaml.safe_dump(META), encoding="utf-8")
    out = tmp_path / "report.md"
    assert cmp.main(["--report", f"yolox_nano={r}", "--meta", str(m), "--out", str(out)]) == 0
    assert "yolox_nano" in out.read_text(encoding="utf-8")
    assert cmp.main(["--report", f"yolox_nano={r}"]) == 0
    assert cmp.main(["--report", "bad"]) == 2
    assert cmp.main([]) == 2
    assert "error" in capsys.readouterr().err


def test_template(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "tpl.md"
    assert cmp.main(["--template", "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "| " + " | ".join(cmp.HEADER) + " |" in text
    assert all(f"`{c}`" in text for c in cmp.CANDIDATES) and "bukan validasi klinis" in text
    rows = [ln for ln in text.splitlines() if ln.startswith("| `")]
    assert all(ln.count("|") == len(cmp.HEADER) + 1 for ln in rows)
    assert cmp.main(["--template"]) == 0 and "TEMPLATE" in capsys.readouterr().out


def test_parity_and_license_columns() -> None:
    meta = {"yolox_nano": {"parity_passed": False}, "efficientdet_lite0": {"parity_passed": True}}
    md = cmp.render_markdown({"yolox_nano": fake_report(), "efficientdet_lite0": fake_report(), "rfdetr_nano": fake_report()}, meta)
    rows = {ln.split("|")[1].strip(): ln for ln in md.splitlines() if ln.startswith("| `")}
    assert "| gagal | owner_accepted_risk |" in rows["`yolox_nano`"] and "| lulus | clear |" in rows["`efficientdet_lite0`"]
    assert "| belum diukur | clear |" in rows["`rfdetr_nano`"]
    assert set(cmp.LICENSES) == set(cmp.CANDIDATES)


def test_render_adr(tmp_path: Path) -> None:
    template = cmp.ADR_TEMPLATE.read_text(encoding="utf-8")
    reports = {"yolox_nano": fake_report(), "mediapipe_modelmaker_mobilenetv2_ssd": fake_report(0.7)}
    adr = cmp.render_adr(reports, META, template)
    assert "<!-- compare:" not in adr and "| " + " | ".join(cmp.HEADER) + " |" in adr
    assert f"`{'s' * 64}`" in adr and "TF Model Garden QAT" in adr and "Catatan ekspor" in adr
    assert adr.count("| `yolox_nano` |") == 2  # results table + licence table
    with pytest.raises(cmp.CompareError, match="marker"):
        cmp.render_adr(reports, META, "no markers")
    r = tmp_path / "r.json"
    r.write_text(json.dumps(fake_report()), encoding="utf-8")
    m1, m2 = tmp_path / "m1.yaml", tmp_path / "m2.json"
    m1.write_text(yaml.safe_dump({"yolox_nano": {"int8_size_mb": 3.0, "export_difficulty": "medium"}}), encoding="utf-8")
    m2.write_text(json.dumps({"yolox_nano": {"int8_size_mb": 4.0, "parity_passed": True}}), encoding="utf-8")
    assert cmp.load_meta([m1, m2]) == {"yolox_nano": {"int8_size_mb": 4.0, "export_difficulty": "medium", "parity_passed": True}}
    out = tmp_path / "adr.md"
    assert cmp.main(["--adr", "--report", f"yolox_nano={r}", "--meta", str(m1), "--meta", str(m2), "--out", str(out)]) == 0
    assert "4.0" in out.read_text(encoding="utf-8")
