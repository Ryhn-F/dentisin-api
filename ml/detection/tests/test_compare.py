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
                "caries_cavitated": {"image_level": lvl, "box_level": {"sensitivity": 0.6, "ci95": None}},
                "caries_noncavitated": {"image_level": {"sensitivity": None, "ci95": None}},
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
    assert "| — |" in rows["`yolox_nano`"]  # non-cavitated sensitivity undefined
    assert "decoder outside graph" in md and "patient_id missing" in md


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
