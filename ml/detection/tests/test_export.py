"""export.py with a FAKE TFLite flatbuffer (header only); no conversion is run here."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import export as ex
from license_gate import GateDecision, evaluate_gate, load_gate
from train import load_config

DET = Path(__file__).resolve().parents[1]


def decision(candidate: str = "yolox_nano") -> GateDecision:
    return evaluate_gate(load_gate(), candidate=candidate, datasets=["ahmed2025"], cfg_license_verified=False, allow_unverified=True)


def labels_for(cfg_name: str, **kw: Any) -> dict[str, Any]:
    cfg, _ = load_config(DET / "configs" / f"{cfg_name}.yaml")
    return ex.build_labels(cfg, decision(cfg["candidate"]), version="0.1.0", model_sha256="a" * 64, size_bytes=100,
                           created_at="2026-10-09T00:00:00+00:00", **kw)


ANCHORS = [[0.5, 0.5, 0.1, 0.1]] * 3


@pytest.mark.parametrize(
    ("name", "kw"),
    [("yolox_nano", {}), ("efficientdet_lite0", {}), ("rfdetr_nano", {}), ("mediapipe_modelmaker_mobilenetv2_ssd", {"anchors": ANCHORS})],
)
def test_labels_valid_for_every_candidate(name: str, kw: dict[str, Any]) -> None:
    labels = labels_for(name, **kw)
    assert ex.validate_labels(labels) == []
    assert labels["validation_status"] == "NOT_VALIDATED"
    assert labels["thresholds"] == {"score": {"caries_cavitated": None, "caries_noncavitated": None}, "status": "PLACEHOLDER", "source": None}
    assert [c["may_raise_triage_level"] for c in labels["classes"]] == [True, False]  # FR-32
    assert labels["licenses"]["datasets"][0]["license"] == "CC-BY-4.0"
    assert {a["lang"] for a in labels["attribution"]} == {"id", "en"}
    assert labels["decode_spec"]["document"].endswith("DECODE_SPEC.md")


def test_yolox_labels_details() -> None:
    lab = labels_for("yolox_nano")
    assert lab["input"]["color_order"] == "BGR" and lab["input"]["resize"] == {"method": "letterbox", "align": "top_left", "pad_value": 114,
                                                                                "interpolation": "bilinear"}
    assert lab["output"]["decode"]["strides"] == [8, 16, 32]
    assert lab["output"]["tensors"][0]["shape"] == [1, 3549, 7] and ex.yolox_rows(416, 416, [8, 16, 32]) == 3549
    assert lab["distribution"] == "pilot" and lab["output"]["tensors_verified"] is False


def test_semantic_validation_catches_errors() -> None:
    lab = labels_for("yolox_nano")
    lab["output"]["tensors"][0]["shape"] = [1, 100, 7]
    assert any("yolox raw shape" in p for p in ex.validate_labels(lab))
    lab = labels_for("yolox_nano")
    lab["output"]["tensors"][0]["role"] = "boxes"
    assert any("roles" in p for p in ex.validate_labels(lab))
    lab = labels_for("yolox_nano")
    lab["classes"] = list(reversed(lab["classes"]))
    assert any("classes must be" in p for p in ex.validate_labels(lab))
    lab = labels_for("mediapipe_modelmaker_mobilenetv2_ssd", anchors=ANCHORS)
    lab["output"]["tensors"][0]["shape"] = [1, 7, 4]
    lab["output"]["tensors"][1]["shape"] = [1, 7, 2]
    probs = ex.validate_labels(lab)
    assert any("anchors but raw_boxes" in p for p in probs) and any("raw_scores last dim" in p for p in probs)
    lab = labels_for("efficientdet_lite0")
    lab["input"]["quantization"] = None
    lab["output"]["tensors_verified"] = True
    assert any("quantized input" in p for p in ex.validate_labels(lab))
    lab = labels_for("yolox_nano")
    lab["thresholds"]["status"] = "set_on_val"
    assert any("null thresholds" in p for p in ex.validate_labels(lab))
    lab = labels_for("yolox_nano")
    lab["output"]["decode"] = {**labels_for("rfdetr_nano")["output"]["decode"]}
    assert ex.validate_labels(lab)  # schema rejects decode/format mismatch
    lab = labels_for("yolox_nano")
    del lab["licenses"]
    assert any("licenses" in p for p in ex.validate_labels(lab))


def test_schema_forbids_release_without_evidence() -> None:
    lab = labels_for("yolox_nano")
    lab["distribution"] = "release"
    assert ex.validate_labels(lab), "release labels with NOT_VALIDATED/PLACEHOLDER must be rejected by the schema"


def test_thresholds_set() -> None:
    lab = labels_for("yolox_nano", thresholds={"caries_cavitated": 0.4, "caries_noncavitated": 0.6, "status": "set_on_val", "source": "val sha"})
    assert lab["thresholds"]["status"] == "set_on_val" and ex.validate_labels(lab) == []
    half = labels_for("yolox_nano", thresholds={"caries_cavitated": 0.4, "status": "set_on_val"})
    assert half["thresholds"]["status"] == "PLACEHOLDER"


def test_decode_errors() -> None:
    cfg, _ = load_config(DET / "configs" / "mediapipe_modelmaker_mobilenetv2_ssd.yaml")
    with pytest.raises(ex.ExportError, match="--anchors"):
        ex.build_decode(cfg, None)
    cfg["model"]["output_format"] = "weird"
    with pytest.raises(ex.ExportError, match="unknown output format"):
        ex.build_decode(cfg, None)


def test_tflite_and_size_gate() -> None:
    assert ex.is_tflite(b"\x00\x00\x00\x00TFL3rest") and not ex.is_tflite(b"PK\x03\x04....") and not ex.is_tflite(b"TFL")
    ex.check_size(15 * ex.MIB, 15)
    with pytest.raises(ex.ExportError, match="size gate"):
        ex.check_size(15 * ex.MIB + 1, 15)


def test_model_card_has_banner_and_no_claims() -> None:
    card = ex.render_model_card(labels_for("yolox_nano"), limitations=ex.DEFAULT_LIMITATIONS)
    assert "NOT VALIDATED" in card and "BELUM DIVALIDASI" in card
    assert "bukan diagnosis" in card and "No performance claim" in card
    assert "Sindh" in card and "CC-BY-4.0" in card
    card2 = ex.render_model_card(labels_for("yolox_nano"), eval_summary={"map50": 0.1})
    assert "not** a clinical validation" in card2 and "(none recorded)" in card2


def build(cfg_path: Path, out: Path, tfl: Path, **kw: Any) -> dict[str, Any]:
    return ex.build_bundle(cfg_path, out, tflite_in=tfl, allow_unverified_license=True, now=None, **kw)


def test_build_bundle_end_to_end(tmp_path: Path, fake_tflite: Callable[[int], Path], write_cfg: Callable[..., Path]) -> None:
    out = tmp_path / "exports" / "0.1.0"
    rep = tmp_path / "report.json"
    rep.write_text(json.dumps({"inputs": {"split": "test", "split_sha256": "b" * 64}, "overall": {
        "map50": 0.5, "per_class": {"caries_cavitated": {"image_level": {"sensitivity": 0.5, "specificity": 0.5}}}}}), encoding="utf-8")
    parity = tmp_path / "parity.json"
    parity.write_text(json.dumps({"passed": True}), encoding="utf-8")
    labels = build(write_cfg(), out, fake_tflite(1000), eval_report=rep, parity_report=parity, run_id="run1")
    assert labels["distribution"] == "internal_experiment_only"
    assert sorted(p.name for p in out.iterdir()) == ["MODEL_CARD.md", "SHA256", "labels.json", "model.tflite"]
    assert ex.verify_bundle(out) == []
    assert labels["model"]["eval_report_sha256"] == ex.sha256_bytes(rep.read_bytes())
    assert "INT8 parity" in (out / "MODEL_CARD.md").read_text(encoding="utf-8")
    sha_lines = (out / "SHA256").read_text(encoding="utf-8").splitlines()
    assert [ln.split("  ")[1] for ln in sha_lines] == list(ex.BUNDLE_FILES)
    # immutability
    with pytest.raises(ex.ExportError, match="immutable"):
        build(write_cfg(), out, fake_tflite(1000))
    build(write_cfg(), out, fake_tflite(1000), force=True)
    # tampering is detected
    (out / "labels.json").write_text((out / "labels.json").read_text(encoding="utf-8").replace("NOT_VALIDATED", "VALIDATED"), encoding="utf-8")
    assert any("sha256 mismatch for labels.json" in p for p in ex.verify_bundle(out))


@pytest.mark.parametrize(
    ("kw", "msg"),
    [
        ({"max_size_mb": 0.0005}, "size gate"),
        ({"release": True}, "license gate refused"),
    ],
)
def test_build_refusals(tmp_path: Path, fake_tflite: Callable[[int], Path], write_cfg: Callable[..., Path], kw: dict[str, Any], msg: str) -> None:
    with pytest.raises(ex.ExportError, match=msg):
        build(write_cfg(), tmp_path / "e" / "0.1.0", fake_tflite(1000), **kw)


def test_build_input_errors(tmp_path: Path, fake_tflite: Callable[[int], Path], write_cfg: Callable[..., Path]) -> None:
    with pytest.raises(ex.ExportError, match="semver"):
        build(write_cfg(), tmp_path / "latest", fake_tflite(64))
    not_tfl = tmp_path / "x.tflite"
    not_tfl.write_bytes(b"PK\x03\x04" + b"\x00" * 20)
    with pytest.raises(ex.ExportError, match="not a TFLite"):
        build(write_cfg(), tmp_path / "1.0.0", not_tfl)
    with pytest.raises(ex.ExportError, match="--tflite-in"):
        ex.build_bundle(write_cfg(), tmp_path / "1.0.0", allow_unverified_license=True)
    with pytest.raises(ex.ExportError, match="int8"):
        build(write_cfg(export__int8=False), tmp_path / "1.0.0", fake_tflite(64))
    with pytest.raises(ex.ExportError, match="license gate refused"):
        ex.build_bundle(write_cfg(), tmp_path / "1.0.0", tflite_in=fake_tflite(64))


def test_release_blockers_listed(write_cfg: Callable[..., Path]) -> None:
    cfg, _ = load_config(write_cfg())
    blockers = ex.release_blockers(cfg, decision(), labels_for("yolox_nano"))
    text = " ".join(blockers)
    for m in ("license gate", "thresholds", "tensors", "output format", "clinical validation"):
        assert m in text


def test_verify_bundle_problems(tmp_path: Path) -> None:
    b = tmp_path / "0.2.0"
    b.mkdir()
    assert "missing model.tflite" in ex.verify_bundle(b)
    (b / "model.tflite").write_bytes(b"nope" * 4)
    lab = labels_for("yolox_nano")
    (b / "labels.json").write_text(json.dumps(lab), encoding="utf-8")
    (b / "MODEL_CARD.md").write_text("x", encoding="utf-8")
    ex.write_sha256(b)
    with (b / "SHA256").open("a", encoding="utf-8") as fh:
        fh.write("00  evil.bin\n")
    probs = ex.verify_bundle(b, max_size_mb=0.000001)
    for m in ("unexpected file", "not a TFLite", "size gate", "model.sha256", "semver"):
        assert any(m in p for p in probs), m


def test_cli(tmp_path: Path, fake_tflite: Callable[[int], Path], write_cfg: Callable[..., Path], capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "0.3.0"
    args = ["--cfg", str(write_cfg()), "--format", "tflite", "--int8", "--out", str(out), "--tflite-in", str(fake_tflite(128))]
    assert ex.main(args) == 2  # license gate
    assert ex.main([*args, "--allow-unverified-license"]) == 0
    assert json.loads(capsys.readouterr().out)["distribution"] == "internal_experiment_only"
    assert ex.main(["verify-bundle", str(out)]) == 0
    (out / "MODEL_CARD.md").write_text("tampered", encoding="utf-8")
    assert ex.main(["verify-bundle", str(out)]) == 1
