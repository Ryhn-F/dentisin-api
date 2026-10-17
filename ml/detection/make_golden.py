"""Golden runtime fixture for the app team: one deterministic input tensor, the RAW output of the real ``model.tflite`` on it,
and the decoded detections -- so the app can prove on a phone that (a) its LiteRT binding feeds the tensor correctly and
(b) its TypeScript decoder matches ``decode.py`` on the REAL output distribution (not just the synthetic vectors).

    uv run python make_golden.py --bundle ../runs/kaggle/yolox_nano/<tgl>/download_yolox_nano/0.1.0-dev.2 \
        --out ../../docs/api/examples/detector/golden/golden_runtime.json

Needs a LiteRT runtime (``ai-edge-litert``; Linux, macOS and Windows wheels exist); the checked-in fixture is verified in CI without a
model: ``tests/test_golden.py`` recomputes every expected detection from the stored raw tensor with ``decode.py``.

The input is NOT a photo (no dataset image may be committed): an integer-only pattern, so the numbers are bit-identical in every
language. The detections it produces mean nothing clinically; this is a runtime/decoder parity test.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from decode import Decoded, decode_yolox, letterbox_params, postprocess, unletterbox

FIXTURE_VERSION = 1
SIZE = 416
#: Source "photo" size for the box-mapping part of the fixture (4:3 phone photo).
PHOTO = (4000, 3000)
#: The synthetic input is not a tooth, so the bundle's thresholds give no detection. A second case lowers one common threshold to the
#: score of the K-th best raw candidate (rounded DOWN to 4 decimals) so that boxes, NMS and the photo mapping are exercised too.
TOP_K = 12
#: Comparison tolerances for the APP (different CPU kernels give slightly different float32): raw logits, final scores, boxes in photo px.
TOLERANCE = {"raw_abs": 1e-3, "score_abs": 1e-4, "box_photo_px_abs": 0.5, "decode_from_fixture_raw_abs": 1e-6}

PATTERN = (
    "BGR float32 NHWC [1,416,416,3]; for y,x in 0..415 and channel c in 0..2 (integers only): "
    "v = (x*3 + y*5 + c*17) % 256; if (x-208)^2 + (y-208)^2 < 80^2: v = min(255, v + 90); tensor[0,y,x,c] = v"
)


def make_input() -> npt.NDArray[np.float32]:
    """The deterministic input tensor described by :data:`PATTERN`."""
    y, x, c = np.meshgrid(np.arange(SIZE), np.arange(SIZE), np.arange(3), indexing="ij")
    v = (x * 3 + y * 5 + c * 17) % 256
    blob = (x - 208) ** 2 + (y - 208) ** 2 < 80**2
    v = np.where(blob, np.minimum(255, v + 90), v)
    return v.astype(np.float32)[None, ...]


def tensor_b64(a: npt.NDArray[np.float32]) -> str:
    return base64.b64encode(np.ascontiguousarray(a, dtype="<f4").tobytes()).decode("ascii")


def tensor_from_b64(s: str, shape: tuple[int, ...]) -> npt.NDArray[np.float32]:
    return np.frombuffer(base64.b64decode(s), dtype="<f4").reshape(shape).copy()


def sha256_f32(a: npt.NDArray[np.float32]) -> str:
    return hashlib.sha256(np.ascontiguousarray(a, dtype="<f4").tobytes()).hexdigest()


def decode_spec(labels: dict[str, Any]) -> dict[str, Any]:
    """Everything ``detections`` needs, taken from labels.json (and stored in the fixture so it is self-contained)."""
    out, pp = labels["output"], labels["output"]["postprocess"]
    return {"input_size": [labels["input"]["width"], labels["input"]["height"]], "photo_size": list(PHOTO),
            "strides": out["decode"]["strides"], "sigmoid_applied": out["decode"]["sigmoid_applied"],
            "resize_align": labels["input"]["resize"]["align"], "nms_iou_threshold": pp["nms_iou_threshold"],
            "class_agnostic_nms": pp["class_agnostic_nms"], "max_detections": pp["max_detections"],
            "class_names": [c["name"] for c in labels["classes"]]}


def top_k_threshold(raw: npt.NDArray[np.float32], spec: dict[str, Any], k: int = TOP_K) -> float:
    w, h = spec["input_size"]
    d = decode_yolox(raw, w, h, spec["strides"], sigmoid_applied=spec["sigmoid_applied"])
    best = np.sort(d.scores)[::-1]
    return float(np.floor(best[min(k, len(best)) - 1] * 1e4) / 1e4)


def detections(raw: npt.NDArray[np.float32], spec: dict[str, Any], thresholds: dict[str, float]) -> list[dict[str, Any]]:
    """raw tensor -> detections in PHOTO pixels, exactly the chain the app runs (DECODE_SPEC sections 3.1, 4, 5)."""
    w, h = spec["input_size"]
    names = spec["class_names"]
    d: Decoded = decode_yolox(raw, w, h, spec["strides"], sigmoid_applied=spec["sigmoid_applied"])
    kept = postprocess(d, [thresholds[n] for n in names], spec["nms_iou_threshold"], spec["max_detections"],
                       class_agnostic=spec["class_agnostic_nms"])
    pw, ph = spec["photo_size"]
    boxes = unletterbox(kept.boxes, letterbox_params(pw, ph, w, h, spec["resize_align"]), pw, ph)
    return [{"class_id": int(kept.classes[i]), "class": names[int(kept.classes[i])], "score": float(kept.scores[i]),
             "box_photo_px": [float(v) for v in boxes[i]]} for i in range(len(kept.scores))]


def run_model(model: Path, x: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:  # pragma: no cover - needs a LiteRT runtime and a model
    try:
        from ai_edge_litert.interpreter import Interpreter
    except ImportError:
        from tensorflow.lite.python.interpreter import Interpreter
    it = Interpreter(model_path=str(model))
    it.allocate_tensors()
    inp, outp = it.get_input_details()[0], it.get_output_details()[0]
    if list(inp["shape"]) != [1, SIZE, SIZE, 3] or np.dtype(inp["dtype"]) != np.float32:
        raise SystemExit(f"unexpected input {inp['shape']} {inp['dtype']}: this fixture is for the float32 YOLOX bundle")
    it.set_tensor(inp["index"], x)
    it.invoke()
    return np.asarray(it.get_tensor(outp["index"]), dtype=np.float32)


def build(bundle: Path, raw: npt.NDArray[np.float32] | None = None) -> dict[str, Any]:
    """The fixture for ``bundle`` (``model.tflite`` + ``labels.json``). ``raw`` replaces the model run (tests)."""
    labels = json.loads((bundle / "labels.json").read_text(encoding="utf-8"))
    model = bundle / "model.tflite"
    x = make_input()
    if raw is None:  # pragma: no cover - needs a LiteRT runtime
        raw = run_model(model, x)
    shape = tuple(labels["output"]["tensors"][0]["shape"])
    if tuple(raw.shape) != shape:
        raise SystemExit(f"model output {raw.shape} != labels.json {shape}")
    spec = decode_spec(labels)
    names = spec["class_names"]
    bundle_thr = {n: labels["thresholds"]["score"][n] for n in names}
    relaxed = dict.fromkeys(names, top_k_threshold(raw, spec))
    return {
        "fixture_version": FIXTURE_VERSION,
        "purpose": "runtime + decoder parity on the REAL model output; NOT a photo and NOT a clinical result",
        "model": {"version": labels["model"]["version"], "run_id": labels["model"].get("run_id"),
                  "sha256": hashlib.sha256(model.read_bytes()).hexdigest() if model.is_file() else None,
                  "labels_sha256": hashlib.sha256((bundle / "labels.json").read_bytes()).hexdigest()},
        "input": {"shape": [1, SIZE, SIZE, 3], "dtype": "float32", "color_order": "BGR", "pattern": PATTERN, "sha256_float32_le": sha256_f32(x)},
        "output": {"shape": list(shape), "dtype": "float32", "raw_float32_le_base64": tensor_b64(raw)},
        "tolerance": TOLERANCE,
        "decode": spec,
        "cases": [
            {"id": "bundle_thresholds", "thresholds": bundle_thr, "expected": detections(raw, spec, bundle_thr)},
            {"id": "top_k_threshold", "thresholds": relaxed, "expected": detections(raw, spec, relaxed)},
        ],
    }


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - needs a LiteRT runtime and a model
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bundle", type=Path, required=True, help="folder with model.tflite + labels.json")
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    fx = build(args.bundle)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(fx, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {args.out} ({args.out.stat().st_size} bytes); detections: " + ", ".join(f"{c['id']}={len(c['expected'])}" for c in fx["cases"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
