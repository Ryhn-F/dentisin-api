"""Reference vectors for the TypeScript port of decode.py (``tests/decode_vectors.json``).

Inputs are stored explicitly (small arrays, rounded to 6 decimals); expected outputs are produced
by decode.py. ``python decode_vectors.py --write`` regenerates the file; a test fails if the stored
file and the current implementation disagree.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np

import decode as d

VECTORS_PATH = Path(__file__).resolve().parent / "tests" / "decode_vectors.json"
SCORE_THRESHOLDS = [0.3, 0.3]
NMS_IOU = 0.45
MAX_DETECTIONS = 10
POST: dict[str, Any] = {"score_thresholds": SCORE_THRESHOLDS, "nms_iou": NMS_IOU, "max_detections": MAX_DETECTIONS}


def _r(a: Any) -> Any:
    return np.round(np.asarray(a, dtype=np.float64), 6)


def _dec(x: d.Decoded) -> dict[str, Any]:
    return {"boxes": x.boxes.tolist(), "classes": x.classes.tolist(), "scores": x.scores.tolist()}


def _post(x: d.Decoded, agnostic: bool = False) -> dict[str, Any]:
    return _dec(d.postprocess(x, SCORE_THRESHOLDS, NMS_IOU, MAX_DETECTIONS, class_agnostic=agnostic))


def build() -> dict[str, Any]:
    vectors: list[dict[str, Any]] = []
    # YOLOX: 64x64 input, strides 8/16/32 -> 64 + 16 + 4 = 84 rows, 2 classes
    i = np.arange(84)
    raw = _r(np.stack([((i * 7) % 11) / 10 - 0.5, ((i * 5) % 13) / 12 - 0.5, ((i % 5) - 2) / 4, ((i % 7) - 3) / 6,
                       ((i * 3) % 10) / 10, ((i * 7) % 10) / 10, ((i * 11) % 10) / 10], axis=1))
    y = d.decode_yolox(raw, 64, 64, (8, 16, 32), sigmoid_applied=True)
    vectors.append({"id": "yolox_64", "decode": {"type": "yolox_raw", "strides": [8, 16, 32], "sigmoid_applied": True},
                    "input_size": [64, 64], "outputs": {"raw": raw.tolist()}, "expected_decoded": _dec(y),
                    "expected_post": _post(y), "expected_post_agnostic": _post(y, True)})
    # SSD raw anchors (6 anchors, background column 0, sigmoid)
    anchors = _r([[0.25, 0.25, 0.5, 0.5], [0.25, 0.75, 0.5, 0.5], [0.75, 0.25, 0.5, 0.5], [0.75, 0.75, 0.5, 0.5],
                  [0.5, 0.5, 1.0, 1.0], [0.5, 0.5, 0.3, 0.6]])
    rb = _r([[0, 0, 0, 0], [1, -1, 0.5, -0.5], [-2, 2, 1, 1], [0.3, 0.3, -1, -1], [0, 0, -2, -2], [0.1, -0.1, 0.2, 0.1]])
    rs = _r([[0, 2, -2], [0, -1, 1.5], [0, -3, -3], [0, 0.5, 0.4], [0, 3, -1], [0, 2.9, -1]])
    s = d.decode_ssd_raw(rb, rs, anchors, 64, 64, background_class=True)
    vectors.append({"id": "ssd_raw_64", "decode": {"type": "ssd_raw_anchors", "anchors": anchors.tolist(), "scales": [10.0, 10.0, 5.0, 5.0],
                                                 "box_order": "yxhw", "score_activation": "sigmoid", "background_class": True},
                    "input_size": [64, 64], "outputs": {"raw_boxes": rb.tolist(), "raw_scores": rs.tolist()},
                    "expected_decoded": _dec(s), "expected_post": _post(s)})
    # SSD post-processed (TFLite_Detection_PostProcess), count = 2 of 3 slots
    pb = _r([[0.1, 0.2, 0.5, 0.6], [0.0, 0.0, 1.0, 1.0], [0.3, 0.3, 0.4, 0.4]])
    p = d.decode_ssd_postprocessed(pb, [0, 1, 0], [0.9, 0.2, 0.8], 2, 320, 240)
    vectors.append({"id": "ssd_post_320x240", "decode": {"type": "ssd_postprocessed", "class_id_offset": 0}, "input_size": [320, 240],
                    "outputs": {"boxes": pb.tolist(), "classes": [0, 1, 0], "scores": [0.9, 0.2, 0.8], "count": [2]},
                    "expected_decoded": _dec(p), "expected_post": _post(p)})
    # DETR (4 queries)
    db = _r([[0.5, 0.5, 0.2, 0.2], [0.51, 0.5, 0.2, 0.2], [0.1, 0.1, 0.05, 0.1], [0.8, 0.8, 0.3, 0.3]])
    dl = _r([[2, -2], [1.5, -3], [-4, -4], [-1, 0.8]])
    t = d.decode_detr(db, dl, 64, 64)
    vectors.append({"id": "detr_64", "decode": {"type": "detr_raw"}, "input_size": [64, 64],
                    "outputs": {"boxes": db.tolist(), "logits": dl.tolist()}, "expected_decoded": _dec(t), "expected_post": _post(t)})
    geometry = []
    for align in ("top_left", "center"):
        lb = d.letterbox_params(200, 100, 64, 64, align)
        geometry.append({"src": [200, 100], "dst": [64, 64], "align": align, "scale": lb.scale, "pad_x": lb.pad_x, "pad_y": lb.pad_y,
                         "box_in": [10, 20, 30, 40], "box_out": d.unletterbox(np.array([10, 20, 30, 40.0]), lb, 200, 100)[0].tolist()})
    quant = {"dequantize": {"q": [-128, 0, 5, 127], "scale": 0.05, "zero_point": -3,
                            "expected": d.dequantize([-128, 0, 5, 127], 0.05, -3).tolist()},
             "quantize_uint8": {"x": [-1.0, 0.0, 0.5, 0.25, 1.0], "scale": 1 / 127.5, "zero_point": 128,
                                "expected": d.quantize_input([-1.0, 0.0, 0.5, 0.25, 1.0], 1 / 127.5, 128, "uint8").tolist()}}
    return {"spec_version": d.SPEC_VERSION, "tolerance": {"abs": 1e-6}, "postprocess": POST, "vectors": vectors,
            "geometry": geometry, "quantization": quant}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--write", action="store_true")
    args = p.parse_args(argv)
    text = json.dumps(build(), indent=1) + "\n"
    if args.write:
        VECTORS_PATH.write_text(text, encoding="utf-8", newline="\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
