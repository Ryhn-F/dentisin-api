"""preprocess.py, predict.py, thresholds.py and the parity decoder -- synthetic images/outputs only."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import check_int8_parity as cp
import export as ex
import predict as pr
import preprocess as pp
import thresholds as th
from canonical import Box, ImageAnnotation, write_jsonl
from license_gate import evaluate_gate, load_gate
from train import load_config

DET = Path(__file__).resolve().parents[1]


def labels_for(name: str, **kw: Any) -> dict[str, Any]:
    cfg, _ = load_config(DET / "configs" / f"{name}.yaml")
    dec = evaluate_gate(load_gate(), candidate=cfg["candidate"], datasets=["ahmed2025"], cfg_license_verified=False, allow_unverified=True)
    return ex.build_labels(cfg, dec, version="0.1.0", model_sha256="a" * 64, size_bytes=1, created_at="2026-10-09T00:00:00+00:00", **kw)


def spec(**kw: Any) -> pp.InputSpec:
    base: dict[str, Any] = {"width": 8, "height": 8, "color_order": "RGB", "pixel_range": (0.0, 255.0), "mean": (0.0, 0.0, 0.0),
                            "std": (1.0, 1.0, 1.0), "resize_method": "letterbox", "align": "top_left", "pad_value": 114,
                            "interpolation": "nearest"}
    base.update(kw)
    return pp.InputSpec(**base)


def test_yolox_config_uses_logits() -> None:
    assert labels_for("yolox_nano")["output"]["decode"]["sigmoid_applied"] is False


def test_input_spec_from_config_equals_from_labels() -> None:
    cfg, _ = load_config(DET / "configs" / "yolox_nano.yaml")
    assert pp.InputSpec.from_config(cfg) == pp.InputSpec.from_labels(labels_for("yolox_nano"))
    s = pp.InputSpec.from_config(cfg)
    assert (s.width, s.color_order, s.resize_method, s.pad_value) == (416, "BGR", "letterbox", 114)


def test_letterbox_top_left_bgr_and_pad() -> None:
    img = np.zeros((4, 8, 3), dtype=np.uint8)
    img[..., 0] = 200  # red channel
    x, g = pp.preprocess(img, spec(color_order="BGR"))
    assert x.shape == (1, 8, 8, 3) and x.dtype == np.float32
    assert g.letterbox is not None and g.letterbox.scale == 1.0 and (g.src_w, g.src_h) == (8, 4)
    assert x[0, 0, 0].tolist() == [0.0, 0.0, 200.0]  # BGR: red moved to the last channel
    assert x[0, 7, 0].tolist() == [114.0, 114.0, 114.0]  # bottom padding


def test_stretch_center_and_normalisation() -> None:
    img = np.full((10, 20, 3), 255, dtype=np.uint8)
    x, g = pp.preprocess(img, spec(resize_method="stretch", align="none", pixel_range=(0.0, 1.0), mean=(0.5, 0.5, 0.5), std=(0.5, 0.5, 0.5)))
    assert g.letterbox is None and np.allclose(x, 1.0)
    x2, g2 = pp.preprocess(img, spec(align="center", pad_value=0))
    assert g2.letterbox is not None and g2.letterbox.pad_y == 2.0 and x2[0, 0, 0, 0] == 0.0 and x2[0, 4, 4, 0] == 255.0


@pytest.mark.parametrize(("kw", "msg"), [({"resize_method": "crop"}, "resize"), ({"color_order": "RGBA"}, "color")])
def test_preprocess_errors(kw: dict[str, Any], msg: str) -> None:
    with pytest.raises(ValueError, match=msg):
        pp.preprocess(np.zeros((4, 4, 3), dtype=np.uint8), spec(**kw))
    with pytest.raises(ValueError, match="uint8 RGB"):
        pp.preprocess(np.zeros((4, 4), dtype=np.uint8), spec())


def _dataset(tmp_path: Path, make_image: Callable[..., Path]) -> dict[str, Path]:
    recs = []
    assign = {}
    for i in range(6):
        name = f"img/{i}.png"
        make_image(tmp_path / "root" / name, w=40, h=20, color=(10 * i, 0, 0))
        boxes = [Box("caries_permanent", 4.0, 4.0, 12.0, 12.0)] if i % 2 == 0 else []
        recs.append(ImageAnnotation(name, 40, 20, boxes, source="ahmed2025"))
        assign[name] = ("train", "train", "val", "val", "test", "test")[i]
    write_jsonl(recs, tmp_path / "canonical.jsonl")
    (tmp_path / "splits.json").write_text(json.dumps({"images": assign}), encoding="utf-8")
    return {"gt": tmp_path / "canonical.jsonl", "splits": tmp_path / "splits.json", "root": tmp_path / "root"}


def test_split_pick_calibration_and_size_check(tmp_path: Path, make_image: Callable[..., Path]) -> None:
    d = _dataset(tmp_path, make_image)
    recs = pp.split_records(d["gt"], d["splits"], "train")
    assert [r.image for r in recs] == ["img/0.png", "img/1.png"]
    assert pp.pick(recs, 1, 7) == pp.pick(list(reversed(recs)), 1, 7) and pp.pick(recs, -1, 7) == []
    arr = pp.build_calibration(recs, d["root"], spec())
    assert arr.shape == (2, 8, 8, 3) and arr.dtype == np.float32
    with pytest.raises(ValueError, match="no images"):
        pp.split_records(d["gt"], d["splits"], "nope")
    with pytest.raises(ValueError, match="no calibration"):
        pp.build_calibration([], d["root"], spec())
    bad = ImageAnnotation("img/0.png", 99, 20)
    with pytest.raises(ValueError, match="EXIF rotation"):
        pp.check_size(pp.load_rgb(d["root"] / "img/0.png"), bad)


def test_calib_cli(tmp_path: Path, make_image: Callable[..., Path], write_cfg: Callable[..., Path]) -> None:
    d = _dataset(tmp_path, make_image)
    cfg = write_cfg()
    out = tmp_path / "calib.npy"
    args = ["calib", "--cfg", str(cfg), "--gt", str(d["gt"]), "--splits", str(d["splits"]), "--images-root", str(d["root"]), "--out", str(out)]
    assert pp.main([*args, "--n", "5"]) == 0
    assert np.load(out).shape == (2, 416, 416, 3) and json.loads(out.with_suffix(".json").read_text())["split"] == "train"
    assert pp.main([*args, "--split", "val", "--n", "0"]) == 2  # no images -> error
    (tmp_path / "bad.yaml").write_text("trainer: x\n", encoding="utf-8")
    assert pp.main(["calib", "--cfg", str(tmp_path / "bad.yaml"), "--out", str(out)]) == 2


def yolox_raw_with_box(cx: float, cy: float, score: float) -> np.ndarray:
    """One confident stride-8 cell at grid (cx//8, cy//8) whose box is 16x16 px centred at (cx, cy).
    obj/cls are LOGITS (configs/yolox_nano.yaml decode.sigmoid_applied: false); every other row has obj logit -30."""
    raw = np.zeros((1, 3549, 7), dtype=np.float64)
    raw[0, :, 4] = -30.0
    raw[0, :, 5:] = -30.0
    gx, gy = int(cx // 8), int(cy // 8)
    row = gy * 52 + gx
    raw[0, row, :4] = [cx / 8 - gx, cy / 8 - gy, np.log(2.0), np.log(2.0)]
    raw[0, row, 4] = 30.0  # sigmoid -> 1.0
    raw[0, row, 5] = np.log(score / (1.0 - score))
    return raw


def test_predict_records_maps_back_to_photo(tmp_path: Path, make_image: Callable[..., Path]) -> None:
    d = _dataset(tmp_path, make_image)
    labels = labels_for("yolox_nano")
    raw = yolox_raw_with_box(52.0, 52.0, 0.9)
    rows = pr.predict_records(lambda x: [raw], labels, pp.split_records(d["gt"], d["splits"], "val"), d["root"])
    assert [r["image"] for r in rows] == ["img/2.png", "img/3.png"]
    b = rows[0]["boxes"][0]
    r = 416 / 40  # letterbox scale for a 40x20 photo
    assert b["cls"] == "caries_permanent" and b["score"] == pytest.approx(0.9)
    assert b["x1"] == pytest.approx(44 / r) and b["x2"] == pytest.approx(60 / r)
    # a detection entirely in the padding (below the photo) is clipped away
    pad = pr.predict_records(lambda x: [yolox_raw_with_box(52.0, 400.0, 0.9)], labels, pp.split_records(d["gt"], d["splits"], "val"), d["root"])
    assert pad[0]["boxes"] == []
    n = pr.write_jsonl(rows, tmp_path / "o" / "p.jsonl")
    assert n == 2 and (tmp_path / "o" / "p.jsonl").read_text().count("\n") == 2


def test_predict_stretch_and_output_count() -> None:
    labels = labels_for("efficientdet_lite0")
    dec = pr.make_decoder(labels)
    with pytest.raises(ValueError, match="outputs"):
        dec([np.zeros(1)])
    boxes = np.array([[[0.1, 0.2, 0.5, 0.6]]], dtype=np.float32)
    rec = ImageAnnotation("a.png", 100, 50)
    rows = pr.predict_records(lambda x: [boxes, np.array([[0.0]]), np.array([[0.8]]), np.array([1.0])], labels, [rec], Path("."),
                              load=lambda p: np.zeros((50, 100, 3), dtype=np.uint8))
    b = rows[0]["boxes"][0]
    assert (b["x1"], b["y1"], b["x2"], b["y2"]) == pytest.approx((20.0, 5.0, 60.0, 25.0))


def test_parity_labels_decoder() -> None:
    labels = labels_for("yolox_nano")
    dec = cp.labels_decoder(labels)
    out = dec([yolox_raw_with_box(52.0, 52.0, 0.9)])
    assert len(out.scores) == 1 and out.classes.tolist() == [0]
    assert len(cp.labels_decoder(labels, score=0.95)([yolox_raw_with_box(52.0, 52.0, 0.9)]).scores) == 0
    rep = cp.compare(lambda x: [yolox_raw_with_box(52.0, 52.0, 0.9)], lambda x: [yolox_raw_with_box(52.0, 52.0, 0.85)],
                     [np.zeros((1, 416, 416, 3))], decode=dec)
    assert rep.detection_agreement == 1.0


def test_thresholds_rules() -> None:
    scores = [0.9, 0.8, 0.3, 0.2, 0.1]
    pos = [True, True, True, False, False]
    r = th.choose(scores, pos, 0.6)
    assert r["threshold"] == 0.8 and r["sensitivity"] == pytest.approx(2 / 3) and r["specificity"] == 1.0
    r2 = th.choose(scores, pos, 1.0)
    assert r2["threshold"] == 0.3 and r2["rule"].startswith("highest")
    r3 = th.choose([0.0, 0.0, 0.5], [True, True, False], 0.9)  # target unreachable above 0 -> Youden
    assert r3["rule"].startswith("max_youden") and r3["threshold"] == 1.0
    assert th.choose([0.4], [False], 0.85)["rule"] == "no_positive_val_images"
    assert th.sens_spec([], [], 0.5) == (None, None)


def test_thresholds_cli(tmp_path: Path, make_image: Callable[..., Path]) -> None:
    d = _dataset(tmp_path, make_image)
    preds = tmp_path / "pv.jsonl"
    preds.write_text(json.dumps({"image": "img/2.png", "boxes": [{"cls": "caries_permanent", "x1": 4, "y1": 4, "x2": 12, "y2": 12,
                                                                  "score": 0.7}]}) + "\n", encoding="utf-8")
    out = tmp_path / "thr.json"
    assert th.main(["--pred", str(preds), "--gt", str(d["gt"]), "--splits", str(d["splits"]), "--out", str(out)]) == 0
    res = json.loads(out.read_text())
    assert res["caries_permanent"] == 0.7 and res["caries_primary"] == 1.0 and res["status"] == th.STATUS
    assert th.main(["--pred", str(tmp_path / "missing.jsonl"), "--gt", str(d["gt"]), "--splits", str(d["splits"]), "--out", str(out)]) == 2
    # export accepts the file (non-null thresholds, unreviewed status) but --release would refuse it
    lab = labels_for("yolox_nano", thresholds=res)
    assert ex.validate_labels(lab) == [] and lab["thresholds"]["status"] == th.STATUS
