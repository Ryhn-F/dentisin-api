from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import decode as d
import decode_vectors as dv


def test_dequantize_and_quantize() -> None:
    assert d.dequantize([10, -5], 0.5, 2).tolist() == [4.0, -3.5]
    # round-half-away-from-zero: 0.5/0.5=1 -> 1+0; -0.25/0.5=-0.5 -> -1
    assert d.quantize_input([0.5, -0.25, 1000], 0.5, 0, "int8").tolist() == [1, -1, 127]
    assert d.quantize_input([-1.0, 1.0], 1 / 127.5, 128, "uint8").tolist() == [0, 255]


def test_letterbox_and_inverse() -> None:
    lb = d.letterbox_params(200, 100, 64, 64, "top_left")
    assert (lb.scale, lb.pad_x, lb.pad_y) == (0.32, 0.0, 0.0)
    c = d.letterbox_params(200, 100, 64, 64, "center")
    assert c.pad_y == pytest.approx(16.0) and c.pad_x == 0.0
    box_in = np.array([[0.32 * 10, 16 + 0.32 * 20, 0.32 * 50, 16 + 0.32 * 60]])
    assert d.unletterbox(box_in, c, 200, 100)[0].tolist() == pytest.approx([10, 20, 50, 60])
    assert d.unletterbox(np.array([[-5, -5, 500, 500]]), lb, 200, 100).tolist() == [[0, 0, 200, 100]]
    with pytest.raises(ValueError, match="align"):
        d.letterbox_params(1, 1, 1, 1, "bottom")
    assert d.unstretch(np.array([[32, 32, 64, 64]]), 64, 64, 200, 100).tolist() == [[100, 50, 200, 100]]


def test_iou_matrix_and_nms() -> None:
    boxes = np.array([[0, 0, 10, 10], [1, 0, 11, 10], [20, 20, 30, 30], [0, 0, 10, 10]], dtype=float)
    iou = d.box_iou_matrix(boxes, boxes)
    assert iou[0, 1] == pytest.approx(90 / 110) and iou[0, 2] == 0
    # 3 ties with 0 on score -> lower index (0) kept, 3 suppressed; 1 suppressed by 0 (0.818 > 0.45)
    assert d.nms(boxes, np.array([0.9, 0.8, 0.7, 0.9]), 0.45).tolist() == [0, 2]
    assert d.nms(boxes, np.array([0.9, 0.8, 0.7, 0.9]), 0.9).tolist() == [0, 1, 2]
    assert d.nms(np.zeros((0, 4)), np.zeros(0), 0.5).tolist() == []


def test_yolox_grid_order_and_decode() -> None:
    grid, st = d.yolox_grid(32, 16, (8, 16))
    # stride 8: 2 rows x 4 cols, y-major then x; stride 16: 1 x 2
    assert grid.tolist()[:5] == [[0, 0], [1, 0], [2, 0], [3, 0], [0, 1]]
    assert grid.shape[0] == 8 + 2 and st.tolist()[-2:] == [16, 16]
    raw = np.zeros((10, 7))
    raw[5] = [0.5, 0.5, 0, math.log(2), 0.8, 0.25, 0.5]  # row 5 = stride 8, gx=1, gy=1
    out = d.decode_yolox(raw[None], 32, 16, (8, 16))
    # cx = (0.5+1)*8 = 12, cy = 12, w = 8, h = 16 -> [8, 4, 16, 20]; score = 0.8*0.5, class 1
    assert out.boxes[5].tolist() == pytest.approx([8, 4, 16, 20])
    assert out.classes[5] == 1 and out.scores[5] == pytest.approx(0.4)
    logits = d.decode_yolox(np.zeros((10, 7)), 32, 16, (8, 16), sigmoid_applied=False)
    assert logits.scores[0] == pytest.approx(0.25)  # sigmoid(0)^2
    with pytest.raises(ValueError, match="expected 10 rows"):
        d.decode_yolox(np.zeros((9, 7)), 32, 16, (8, 16))


def test_ssd_raw_decode_hand() -> None:
    anchors = [[0.5, 0.5, 0.2, 0.4]]  # yc, xc, h, w
    out = d.decode_ssd_raw([[0, 0, 0, 0]], [[0.0, 0.0]], anchors, 100, 50, score_activation="none")
    # no offset -> box = anchor: x 0.3..0.7, y 0.4..0.6
    assert out.boxes[0].tolist() == pytest.approx([30, 20, 70, 30])
    out2 = d.decode_ssd_raw([[0, 1, 0, 0]], [[1.0, 3.0]], anchors, 100, 50, box_order="xywh", score_activation="softmax")
    # xywh: tx=0, ty=1 -> yc = 1/10*0.2 + 0.5 = 0.52
    assert out2.boxes[0, 1] == pytest.approx((0.52 - 0.1) * 50)
    assert out2.classes[0] == 1 and out2.scores[0] == pytest.approx(math.exp(3) / (math.exp(1) + math.exp(3)))
    cases: list[tuple[dict[str, Any], str]] = [({"box_order": "abcd"}, "box_order"), ({"score_activation": "relu"}, "score_activation")]
    for kw, msg in cases:
        with pytest.raises(ValueError, match=msg):
            d.decode_ssd_raw([[0, 0, 0, 0]], [[0.0]], anchors, 1, 1, **kw)
    with pytest.raises(ValueError, match="anchors"):
        d.decode_ssd_raw([[0, 0, 0, 0]] * 2, [[0.0]] * 2, anchors, 1, 1)


def test_ssd_post_and_detr() -> None:
    p = d.decode_ssd_postprocessed([[0.1, 0.2, 0.5, 0.6], [0, 0, 1, 1]], [1, 0], [0.9, 0.1], 1, 100, 50, class_id_offset=1)
    assert p.boxes.tolist() == [[20, 5, 60, 25]] and p.classes.tolist() == [0]
    t = d.decode_detr([[0.5, 0.5, 0.2, 0.4]], [[0.0, 2.0]], 100, 50)
    assert t.boxes[0].tolist() == pytest.approx([40, 15, 60, 35]) and t.classes[0] == 1


def test_postprocess_thresholds_classes_and_cap() -> None:
    dec = d.Decoded(
        boxes=np.array([[0, 0, 10, 10], [0, 0, 10, 10], [1, 0, 11, 10], [50, 50, 60, 60], [0, 0, 5, 5]], dtype=float),
        classes=np.array([0, 1, 0, 0, 7]),
        scores=np.array([0.9, 0.8, 0.85, 0.2, 0.99]),
    )
    out = d.postprocess(dec, [0.5, 0.5], 0.45, 10)
    # class 7 invalid dropped; 0.2 under threshold; per-class NMS keeps class-1 box overlapping class-0
    assert out.scores.tolist() == [0.9, 0.8] and out.classes.tolist() == [0, 1]
    agn = d.postprocess(dec, [0.5, 0.5], 0.45, 10, class_agnostic=True)
    assert agn.scores.tolist() == [0.9]
    assert d.postprocess(dec, [0.0, 0.0], 0.45, 1).scores.tolist() == [0.9]
    empty = d.postprocess(dec, [1.1, 1.1], 0.45, 10)
    assert empty.boxes.shape == (0, 4)


def test_decode_from_spec_dispatch() -> None:
    data = dv.build()
    for v in data["vectors"]:
        outs = {k: np.asarray(x) for k, x in v["outputs"].items()}
        spec: dict[str, Any] = {**v["decode"]}
        spec.setdefault("strides", None)
        got = d.decode_from_spec(outs, spec, *v["input_size"])
        assert got.scores.tolist() == pytest.approx(v["expected_decoded"]["scores"])
    with pytest.raises(ValueError, match="unknown decode type"):
        d.decode_from_spec({}, {"type": "nope"}, 1, 1)


def test_stored_vectors_match_implementation() -> None:
    stored = json.loads(dv.VECTORS_PATH.read_text(encoding="utf-8"))
    fresh = json.loads(json.dumps(dv.build()))

    def close(a: Any, b: Any) -> None:
        if isinstance(a, dict):
            assert a.keys() == b.keys()
            for k in a:
                close(a[k], b[k])
        elif isinstance(a, list):
            assert len(a) == len(b)
            for x, y in zip(a, b, strict=True):
                close(x, y)
        elif isinstance(a, float):
            assert a == pytest.approx(b, abs=1e-9)
        else:
            assert a == b

    close(stored, fresh)
    assert stored["vectors"][0]["expected_post"]["scores"], "yolox vector must produce detections"


def test_vectors_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    assert dv.main([]) == 0
    assert json.loads(capsys.readouterr().out)["spec_version"] == d.SPEC_VERSION
    target = tmp_path / "v.json"
    monkeypatch.setattr(dv, "VECTORS_PATH", target)
    assert dv.main(["--write"]) == 0 and target.is_file()
