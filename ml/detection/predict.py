"""Run a model over a split and write predictions JSONL for ``eval.py`` -- using the SAME
preprocess (preprocess.py, DECODE_SPEC §1) and decode (decode.py, §2-5) the app must implement.

    python predict.py --model bundle/model.tflite --labels bundle/labels.json --split val \
        --gt canonical.jsonl --splits splits.json --images-root <dataset root> --out out/preds_val.jsonl
    python predict.py --model model_float32.tflite --labels bundle/labels.json ...   # float reference
    python predict.py --model model.onnx --onnx-nchw --labels bundle/labels.json ... # ONNX (onnxruntime)

Output lines: ``{"image": <canonical path>, "boxes": [{cls, x1, y1, x2, y2, score}]}`` in ORIGINAL
photo pixels. A low ``--score-floor`` (default 0.001) keeps the full ranking for AP; eval.py
applies the operating threshold itself. NMS IoU and max detections come from labels.json
(``--max-detections`` may raise the cap for AP). Writes no images.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from canonical import CLASS_NAMES, ImageAnnotation
from check_int8_parity import Runner
from decode import Decoded, decode_from_spec, postprocess, unletterbox, unstretch
from preprocess import Geometry, InputSpec, check_size, load_rgb, preprocess, split_records

DecodeFn = Callable[[Sequence[npt.NDArray[Any]]], Decoded]


def make_decoder(labels: Mapping[str, Any]) -> DecodeFn:
    """outputs (interpreter order) -> Decoded in model-input pixels, using labels.json roles."""
    tensors = labels["output"]["tensors"]
    spec = labels["output"]["decode"]
    w, h = int(labels["input"]["width"]), int(labels["input"]["height"])
    by_index = {int(t["index"]): t["role"] for t in tensors}

    def run(outputs: Sequence[npt.NDArray[Any]]) -> Decoded:
        if len(outputs) != len(by_index):
            raise ValueError(f"model returned {len(outputs)} outputs, labels.json lists {len(by_index)}")
        named = {by_index[i]: np.asarray(o) for i, o in enumerate(outputs)}
        return decode_from_spec(named, spec, w, h)

    return run


def to_photo(d: Decoded, geom: Geometry, in_w: int, in_h: int) -> npt.NDArray[np.float64]:
    if geom.method == "letterbox":
        assert geom.letterbox is not None
        return unletterbox(d.boxes, geom.letterbox, geom.src_w, geom.src_h)
    return unstretch(d.boxes, in_w, in_h, geom.src_w, geom.src_h)


def predict_records(
    runner: Runner,
    labels: Mapping[str, Any],
    records: Iterable[ImageAnnotation],
    images_root: Path,
    *,
    score_floor: float = 0.001,
    max_detections: int | None = None,
    load: Callable[[Path], npt.NDArray[np.uint8]] = load_rgb,
) -> list[dict[str, Any]]:
    spec = InputSpec.from_labels(labels)
    decoder = make_decoder(labels)
    post = labels["output"]["postprocess"]
    cap = int(max_detections if max_detections is not None else post["max_detections"])
    out: list[dict[str, Any]] = []
    for rec in records:
        arr = load(images_root / rec.image)
        check_size(arr, rec)
        x, geom = preprocess(arr, spec)
        d = postprocess(decoder(list(runner(x))), [score_floor] * len(CLASS_NAMES), float(post["nms_iou_threshold"]), cap,
                        class_agnostic=bool(post["class_agnostic_nms"]))
        boxes = to_photo(d, geom, spec.width, spec.height)
        dets = []
        for b, c, s in zip(boxes, d.classes, d.scores, strict=True):
            if b[2] - b[0] <= 0 or b[3] - b[1] <= 0:  # fully clipped / degenerate after mapping back
                continue
            dets.append({"cls": CLASS_NAMES[int(c)], "x1": float(b[0]), "y1": float(b[1]), "x2": float(b[2]), "y2": float(b[3]),
                         "score": float(s)})
        out.append({"image": rec.image, "boxes": dets})
    return out


def write_jsonl(rows: Iterable[Mapping[str, Any]], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
            n += 1
    return n


def onnx_runner(model_path: Path, *, nchw: bool = True) -> Runner:  # pragma: no cover - needs onnxruntime
    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise RuntimeError("pip install onnxruntime (Kaggle notebook installs a pinned version)") from exc
    sess = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    name = sess.get_inputs()[0].name

    def run(x: npt.NDArray[Any]) -> list[npt.NDArray[Any]]:
        data = np.transpose(x, (0, 3, 1, 2)) if nchw else x
        return [np.asarray(o) for o in sess.run(None, {name: data.astype(np.float32)})]

    return run


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI over optional runtimes
    from check_int8_parity import tflite_runner

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", type=Path, required=True)
    p.add_argument("--labels", type=Path, required=True)
    p.add_argument("--onnx-nchw", action="store_true", help="ONNX model takes NCHW input (YOLOX export)")
    p.add_argument("--split", required=True, choices=["train", "val", "test"])
    p.add_argument("--gt", type=Path, required=True)
    p.add_argument("--splits", type=Path, required=True)
    p.add_argument("--images-root", type=Path, required=True)
    p.add_argument("--score-floor", type=float, default=0.001)
    p.add_argument("--max-detections", type=int, default=100)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    labels = json.loads(args.labels.read_text(encoding="utf-8"))
    runner = onnx_runner(args.model, nchw=args.onnx_nchw) if args.model.suffix == ".onnx" else tflite_runner(args.model)
    rows = predict_records(runner, labels, split_records(args.gt, args.splits, args.split), args.images_root,
                           score_floor=args.score_floor, max_detections=args.max_detections)
    n = write_jsonl(rows, args.out)
    print(json.dumps({"out": str(args.out), "images": n, "boxes": sum(len(r["boxes"]) for r in rows)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
