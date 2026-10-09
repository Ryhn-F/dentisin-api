"""eval.py against HAND-COMPUTED tiny examples (derivations in comments)."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import jsonschema
import numpy as np
import pytest

import eval as ev
from canonical import AnnotationError, Box, ImageAnnotation, write_jsonl

C, N = "caries_cavitated", "caries_noncavitated"
THR = {C: 0.5, N: 0.5}
SCHEMA = json.loads((Path(__file__).resolve().parents[1] / "report.schema.json").read_text(encoding="utf-8"))


def b(cls: str, x1: float, y1: float, x2: float, y2: float, s: float | None = None) -> Box:
    return Box(cls, x1, y1, x2, y2, s)


def example() -> tuple[list[ImageAnnotation], dict[str, list[Box]], dict[str, dict[str, str]]]:
    gt = [
        ImageAnnotation("img1", 100, 100, [b(C, 0, 0, 10, 10)], source="ahmed2025"),
        ImageAnnotation("img2", 100, 100, [b(N, 20, 20, 30, 30)], source="ahmed2025"),
        ImageAnnotation("img3", 100, 100, [], source="ahmed2025"),
        ImageAnnotation("img4", 100, 100, [], source="local"),
        ImageAnnotation("img5", 100, 100, [b(C, 0, 0, 10, 10), b(C, 20, 0, 30, 10)], source="local"),
    ]
    preds = {
        "img1": [b(C, 0, 0, 10, 10, 0.9), b(C, 50, 50, 60, 60, 0.8), b(N, 0, 0, 10, 10, 0.3)],
        "img2": [],
        "img3": [b(C, 0, 0, 10, 10, 0.6)],
        "img4": [],
        "img5": [b(C, 0, 0, 10, 10, 0.4), b(C, 21, 0, 31, 10, 0.7)],
    }
    meta = {
        "img1": {"patient_id": "P1", "dentition": "primary", "phone_model": "A"},
        "img2": {"patient_id": "P1", "dentition": "primary", "phone_model": "A"},
        "img3": {"patient_id": "P2", "dentition": "permanent", "phone_model": ""},
        "img4": {"patient_id": "P3", "dentition": "permanent", "phone_model": "B"},
        "img5": {"patient_id": "P3", "dentition": "permanent", "phone_model": "B"},
    }
    return gt, preds, meta


def test_iou_and_ratio() -> None:
    assert ev.iou(b(C, 0, 0, 10, 10), b(C, 0, 0, 10, 10)) == 1.0
    assert ev.iou(b(C, 20, 0, 30, 10), b(C, 21, 0, 31, 10)) == pytest.approx(90 / 110)
    assert ev.iou(b(C, 0, 0, 1, 1), b(C, 2, 2, 3, 3)) == 0.0
    assert ev.iou(b(C, 0, 0, 0, 0), b(C, 0, 0, 0, 0)) == 0.0
    assert ev.ratio(1, 0) is None and ev.ratio(1, 4) == 0.25


def test_match_class_greedy_and_ties() -> None:
    g = [b(C, 0, 0, 10, 10)]
    # equal scores: the earlier prediction wins the GT (stable order); the duplicate is FP
    flags, m = ev.match_class(g, [b(C, 0, 0, 10, 10, 0.5), b(C, 0, 0, 10, 10, 0.5)], 0.5)
    assert flags == [True, False] and m == 1
    # higher score claims the GT first even if its IoU is lower
    flags, _ = ev.match_class(g, [b(C, 0, 0, 10, 12, 0.4), b(C, 0, 0, 10, 11, 0.9)], 0.5)
    assert flags == [True, False]
    # best IoU among unmatched GT
    g2 = [b(C, 0, 0, 10, 10), b(C, 2, 0, 12, 10)]
    flags, m = ev.match_class(g2, [b(C, 2, 0, 12, 10, 0.9), b(C, 0, 0, 10, 10, 0.8)], 0.5)
    assert flags == [True, True] and m == 2
    assert ev.match_class(g, [b(C, 50, 50, 60, 60, 0.9)], 0.5) == ([False], 0)


def test_average_precision_hand() -> None:
    # scores 0.9 T, 0.8 F, 0.7 T, 0.6 F, 0.4 T with 3 GT -> 1/3*1 + 1/3*2/3 + 1/3*0.6 = 34/45
    s = np.array([0.9, 0.8, 0.7, 0.6, 0.4])
    t = np.array([True, False, True, False, True])
    assert ev.average_precision(s, t, np.ones(5), 3) == pytest.approx(34 / 45)
    assert ev.average_precision(s, t, np.full(5, 2.0), 6) == pytest.approx(34 / 45)  # weight invariance
    assert ev.average_precision(s, t, np.ones(5), 0) is None
    assert ev.average_precision(np.array([]), np.array([], dtype=bool), np.array([]), 2) == 0.0
    assert ev.average_precision(np.array([0.9]), np.array([False]), np.ones(1), 1) == 0.0


def test_overall_hand_computed() -> None:
    gt, preds, meta = example()
    rep = ev.evaluate(gt, preds, THR, metadata=meta, n_boot=0)
    pc = rep["overall"]["per_class"]
    cav_img = pc[C]["image_level"]
    assert (cav_img["tp"], cav_img["fp"], cav_img["fn"], cav_img["tn"]) == (2, 1, 0, 2)
    assert cav_img["sensitivity"] == 1.0
    assert cav_img["specificity"] == pytest.approx(2 / 3)
    assert cav_img["ppv"] == pytest.approx(2 / 3)
    assert cav_img["npv"] == 1.0
    cav_box = pc[C]["box_level"]
    assert (cav_box["tp"], cav_box["fp"], cav_box["fn"]) == (2, 2, 1)
    assert cav_box["sensitivity"] == pytest.approx(2 / 3) and cav_box["ppv"] == 0.5
    assert cav_box["specificity"] is None and cav_box["npv"] is None
    assert pc[C]["ap50"] == pytest.approx(34 / 45)
    assert pc[C]["n_gt_boxes"] == 3 and pc[C]["n_positive_images"] == 2
    non_img = pc[N]["image_level"]
    assert (non_img["tp"], non_img["fp"], non_img["fn"], non_img["tn"]) == (0, 0, 1, 4)
    assert non_img["sensitivity"] == 0.0 and non_img["ppv"] is None and non_img["npv"] == pytest.approx(0.8)
    assert pc[N]["box_level"]["ppv"] is None
    assert pc[N]["ap50"] == 0.0
    anyl = pc["any_lesion"]["image_level"]
    assert (anyl["tp"], anyl["fp"], anyl["fn"], anyl["tn"]) == (2, 1, 1, 1)
    assert rep["overall"]["map50"] == pytest.approx(17 / 45)
    assert rep["overall"]["box_confusion_matrix"]["matrix"] == [[2, 0, 1], [0, 0, 1], [2, 0, 0]]
    assert rep["counts"]["bootstrap_unit"] == "patient" and rep["counts"]["n_units"] == 3
    assert rep["counts"]["n_pred_boxes"][C] == 5
    assert pc[C]["image_level"]["ci95"] is None and rep["overall"]["map50_ci95"] is None


def test_class_confusion_cavitated_predicted_as_noncavitated() -> None:
    gt = [ImageAnnotation("a", 50, 50, [b(C, 0, 0, 10, 10)])]
    rep = ev.evaluate(gt, {"a": [b(N, 0, 0, 10, 10, 0.9)]}, THR, n_boot=0, subgroups=())
    assert rep["overall"]["box_confusion_matrix"]["matrix"] == [[0, 1, 0], [0, 0, 0], [0, 0, 0]]
    pc = rep["overall"]["per_class"]
    assert pc[C]["image_level"]["fn"] == 1 and pc[N]["image_level"]["fp"] == 1
    assert pc["any_lesion"]["image_level"]["tp"] == 1  # class-agnostic


def test_subgroups() -> None:
    gt, preds, meta = example()
    rep = ev.evaluate(gt, preds, THR, metadata=meta, n_boot=50, seed=1, subgroups=("dentition", "phone_model", "lighting", "source"))
    sub = rep["subgroups"]
    prim = sub["dentition"]["groups"]["primary"]
    assert prim["n_images"] == 2 and prim["n_units"] == 1
    # primary = img1 (cav TP) + img2 (cav negative, no cav prediction): sens 1, spec 1
    assert prim["per_class"][C]["image_level"]["sensitivity"] == 1.0
    assert prim["per_class"][C]["image_level"]["specificity"] == 1.0
    perm = sub["dentition"]["groups"]["permanent"]
    assert perm["per_class"][C]["image_level"]["specificity"] == 0.5  # img3 FP, img4 TN
    assert set(sub["phone_model"]["groups"]) == {"A", "B", "unknown"}
    assert sub["lighting"] == {"status": "column_missing"}
    assert set(sub["source"]["groups"]) == {"ahmed2025", "local"}
    # single-patient subgroup -> every bootstrap replicate is identical -> degenerate CI at the point estimate
    ci = prim["per_class"][C]["image_level"]["ci95"]["sensitivity"]
    assert ci["lo"] == ci["hi"] == 1.0


def test_bootstrap_resamples_patients_not_images() -> None:
    gt = [ImageAnnotation(f"a{i}", 10, 10, [b(C, 0, 0, 5, 5)]) for i in range(10)]
    gt += [ImageAnnotation(f"b{i}", 10, 10, [b(C, 0, 0, 5, 5)]) for i in range(10)]
    preds = {f"a{i}": [b(C, 0, 0, 5, 5, 0.9)] for i in range(10)}  # patient A all found, patient B all missed
    meta = {g.image: {"patient_id": g.image[0]} for g in gt}
    by_patient = ev.evaluate(gt, preds, THR, metadata=meta, n_boot=2000, seed=3, subgroups=())
    ci_p = by_patient["overall"]["per_class"][C]["image_level"]["ci95"]["sensitivity"]
    assert by_patient["overall"]["per_class"][C]["image_level"]["sensitivity"] == 0.5
    assert (ci_p["lo"], ci_p["hi"]) == (0.0, 1.0)
    by_image = ev.evaluate(gt, preds, THR, n_boot=2000, seed=3, subgroups=())
    ci_i = by_image["overall"]["per_class"][C]["image_level"]["ci95"]["sensitivity"]
    assert by_image["counts"]["bootstrap_unit"] == "image"
    assert ci_i["lo"] > 0.2 and ci_i["hi"] < 0.8
    assert any("resamples IMAGES" in w for w in by_image["warnings"])
    # undefined replicate values are skipped, n_valid reported
    spec = by_patient["overall"]["per_class"][C]["image_level"]["ci95"]["specificity"]
    assert spec == {"lo": None, "hi": None, "n_valid": 0}


def test_bootstrap_deterministic() -> None:
    gt, preds, meta = example()
    a = ev.evaluate(gt, preds, THR, metadata=meta, n_boot=200, seed=9)
    c = ev.evaluate(gt, preds, THR, metadata=meta, n_boot=200, seed=9)
    assert a == c


def test_warnings_for_missing_and_extra_predictions() -> None:
    gt = [ImageAnnotation("a", 10, 10, [])]
    rep = ev.evaluate(gt, {"zzz": []}, THR, n_boot=0, subgroups=())
    assert any("no prediction entry" in w for w in rep["warnings"])
    assert any("not in the evaluated GT" in w for w in rep["warnings"])


def write_inputs(tmp: Path) -> dict[str, Path]:
    gt, preds, meta = example()
    write_jsonl(gt, tmp / "gt.jsonl")
    (tmp / "pred.jsonl").write_text("".join(json.dumps({"image": k, "boxes": [x.to_dict() for x in v]}) + "\n" for k, v in preds.items()),
                                    encoding="utf-8")
    with (tmp / "meta.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["image", "patient_id", "dentition", "phone_model"])
        for k, v in meta.items():
            w.writerow([k, v["patient_id"], v["dentition"], v["phone_model"]])
    (tmp / "splits.json").write_text(json.dumps({"images": {g.image: ("test" if g.image != "img4" else "val") for g in gt}}),
                                     encoding="utf-8")
    return {"gt": tmp / "gt.jsonl", "pred": tmp / "pred.jsonl", "meta": tmp / "meta.csv", "splits": tmp / "splits.json"}


def test_report_matches_schema_and_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = write_inputs(tmp_path)
    out = tmp_path / "out" / "report.json"
    rc = ev.main(["--gt", str(p["gt"]), "--pred", str(p["pred"]), "--splits", str(p["splits"]), "--split", "test",
                  "--metadata", str(p["meta"]), "--bootstrap", "100", "--candidate", "yolox_nano", "--report", str(out)])
    assert rc == 0
    rep: dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
    jsonschema.validate(rep, SCHEMA)
    assert rep["counts"]["n_images"] == 4  # img4 is in val
    assert rep["settings"]["score_threshold_status"] == "PLACEHOLDER"
    assert rep["inputs"]["split_sha256"] and "not a clinical validation" in rep["disclaimer"].lower().replace("not a clinical", "not a clinical")
    assert json.loads(capsys.readouterr().out)["report"].endswith("report.json")


def test_thresholds_file_and_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = write_inputs(tmp_path)
    thr = tmp_path / "thr.json"
    thr.write_text(json.dumps({C: 0.35, N: 0.6, "status": "set_on_val"}), encoding="utf-8")
    assert ev.resolve_thresholds(None, thr) == ({C: 0.35, N: 0.6}, "set_on_val")
    assert ev.resolve_thresholds(0.3, None) == ({C: 0.3, N: 0.3}, "PLACEHOLDER")
    thr.write_text(json.dumps({C: 0.3, N: None}), encoding="utf-8")
    with pytest.raises(AnnotationError, match="null"):
        ev.resolve_thresholds(None, thr)
    assert ev.main(["--gt", str(p["gt"]), "--pred", str(p["pred"]), "--thresholds", str(thr), "--report", str(tmp_path / "r.json")]) == 2
    assert ev.main(["--gt", str(p["gt"]), "--pred", str(p["pred"]), "--splits", str(p["splits"]), "--split", "train",
                    "--report", str(tmp_path / "r.json")]) == 2
    assert "no images in split" in capsys.readouterr().err
    with pytest.raises(AnnotationError, match="needs --splits"):
        ev.build_report(gt_path=p["gt"], pred_path=p["pred"], split="test", splits_path=None, metadata_path=None, thresholds=THR,
                        threshold_status="x", iou_thr=0.5, n_boot=0, seed=0, subgroups=())


@pytest.mark.parametrize(
    ("line", "msg"),
    [('{"image": "a", "boxes": [{"cls": "caries_cavitated", "x1": 0, "y1": 0, "x2": 1, "y2": 1}]}', "without score"),
     ('{"image": "a", "boxes": [{"cls": "tooth", "x1": 0, "y1": 0, "x2": 1, "y2": 1, "score": 0.5}]}', "unknown class")],
)
def test_prediction_validation(tmp_path: Path, line: str, msg: str) -> None:
    p = tmp_path / "p.jsonl"
    p.write_text(line + "\n", encoding="utf-8")
    with pytest.raises(AnnotationError, match=msg):
        ev.load_predictions(p)


def test_duplicate_prediction_and_bad_metadata(tmp_path: Path) -> None:
    p = tmp_path / "p.jsonl"
    p.write_text('{"image": "a", "boxes": []}\n\n{"image": "a", "boxes": []}\n', encoding="utf-8")
    with pytest.raises(AnnotationError, match="duplicate"):
        ev.load_predictions(p)
    m = tmp_path / "m.csv"
    m.write_text("file,patient_id\nx,1\n", encoding="utf-8")
    with pytest.raises(AnnotationError, match="'image' column"):
        ev.load_metadata(m)
