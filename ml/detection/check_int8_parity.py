"""Compare float vs INT8 model outputs on sample inputs.

Works with ANY callables ``fn(input_array) -> list[np.ndarray]`` so it can be unit-tested with fakes
and used with real LiteRT interpreters (:func:`tflite_runner`, optional dependency).

Tensor-level: max/mean absolute error and cosine similarity per output tensor.
Detection-level (optional ``decode`` callable -> decode.Decoded after postprocess): fraction of
float detections matched by an INT8 detection of the same class with IoU >= 0.5, and the mean
absolute score difference of matched pairs.

Pass criteria are TECHNICAL DEFAULTS (not clinical): min cosine 0.98, min detection agreement
0.90. They are recorded in the report so they can be tightened after spike S1.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from decode import Decoded, box_iou_matrix

Runner = Callable[[npt.NDArray[Any]], Sequence[npt.NDArray[Any]]]
DecodeFn = Callable[[Sequence[npt.NDArray[Any]]], Decoded]

DEFAULT_MIN_COSINE = 0.98
DEFAULT_MIN_AGREEMENT = 0.90


@dataclass
class TensorStats:
    index: int
    max_abs_err: float
    mean_abs_err: float
    cosine: float | None


@dataclass
class ParityReport:
    n_samples: int
    tensors: list[TensorStats]
    detection_agreement: float | None
    mean_score_abs_diff: float | None
    n_float_detections: int
    min_cosine: float
    min_agreement: float
    passed: bool
    failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def cosine(a: npt.NDArray[Any], b: npt.NDArray[Any]) -> float | None:
    x = np.asarray(a, dtype=np.float64).ravel()
    y = np.asarray(b, dtype=np.float64).ravel()
    na, nb = float(np.linalg.norm(x)), float(np.linalg.norm(y))
    if na == 0.0 and nb == 0.0:
        return 1.0
    if na == 0.0 or nb == 0.0:
        return None
    return float(np.dot(x, y) / (na * nb))


def match_detections(f: Decoded, q: Decoded, iou_thr: float = 0.5) -> tuple[int, list[float]]:
    """Greedy (float score desc) one-to-one matching of same-class detections."""
    if len(f.scores) == 0 or len(q.scores) == 0:
        return 0, []
    ious = box_iou_matrix(f.boxes, q.boxes)
    used: set[int] = set()
    matched = 0
    diffs: list[float] = []
    for i in sorted(range(len(f.scores)), key=lambda k: (-float(f.scores[k]), k)):
        best, best_iou = -1, iou_thr
        for j in range(len(q.scores)):
            if j in used or q.classes[j] != f.classes[i]:
                continue
            if ious[i, j] >= best_iou and (best == -1 or ious[i, j] > best_iou):
                best, best_iou = j, float(ious[i, j])
        if best >= 0:
            used.add(best)
            matched += 1
            diffs.append(abs(float(f.scores[i]) - float(q.scores[best])))
    return matched, diffs


def compare(
    float_fn: Runner,
    int8_fn: Runner,
    samples: Iterable[npt.NDArray[Any]],
    *,
    decode: DecodeFn | None = None,
    min_cosine: float = DEFAULT_MIN_COSINE,
    min_agreement: float = DEFAULT_MIN_AGREEMENT,
) -> ParityReport:
    errs: dict[int, list[npt.NDArray[np.float64]]] = {}
    fl: dict[int, list[npt.NDArray[np.float64]]] = {}
    qt: dict[int, list[npt.NDArray[np.float64]]] = {}
    n = 0
    matched_total = 0
    float_dets = 0
    score_diffs: list[float] = []
    for x in samples:
        n += 1
        fo = [np.asarray(o, dtype=np.float64) for o in float_fn(x)]
        qo = [np.asarray(o, dtype=np.float64) for o in int8_fn(x)]
        if len(fo) != len(qo):
            raise ValueError(f"output count differs: float {len(fo)} vs int8 {len(qo)}")
        for i, (a, b) in enumerate(zip(fo, qo, strict=True)):
            if a.shape != b.shape:
                raise ValueError(f"output {i} shape differs: {a.shape} vs {b.shape}")
            errs.setdefault(i, []).append(np.abs(a - b).ravel())
            fl.setdefault(i, []).append(a.ravel())
            qt.setdefault(i, []).append(b.ravel())
        if decode is not None:
            fd, qd = decode(fo), decode(qo)
            m, d = match_detections(fd, qd)
            matched_total += m
            float_dets += len(fd.scores)
            score_diffs.extend(d)
    if n == 0:
        raise ValueError("no samples")
    stats = [
        TensorStats(
            index=i,
            max_abs_err=float(np.max(np.concatenate(errs[i]))) if np.concatenate(errs[i]).size else 0.0,
            mean_abs_err=float(np.mean(np.concatenate(errs[i]))) if np.concatenate(errs[i]).size else 0.0,
            cosine=cosine(np.concatenate(fl[i]), np.concatenate(qt[i])),
        )
        for i in sorted(errs)
    ]
    failures = [f"tensor {s.index}: cosine {s.cosine} < {min_cosine}" for s in stats if s.cosine is None or s.cosine < min_cosine]
    agreement: float | None = None
    if decode is not None:
        agreement = matched_total / float_dets if float_dets else None
        if agreement is not None and agreement < min_agreement:
            failures.append(f"detection agreement {agreement:.3f} < {min_agreement}")
    return ParityReport(
        n_samples=n,
        tensors=stats,
        detection_agreement=agreement,
        mean_score_abs_diff=float(np.mean(score_diffs)) if score_diffs else None,
        n_float_detections=float_dets,
        min_cosine=min_cosine,
        min_agreement=min_agreement,
        passed=not failures,
        failures=failures,
    )


#: Technical score floor used ONLY to compare float vs INT8 detections (not an operating threshold).
AGREEMENT_SCORE = 0.3


def labels_decoder(labels: dict[str, Any], *, score: float = AGREEMENT_SCORE) -> DecodeFn:
    """Decode + postprocess per labels.json (DECODE_SPEC §3-4) with a fixed technical score floor."""
    from decode import postprocess
    from predict import make_decoder

    raw = make_decoder(labels)
    post = labels["output"]["postprocess"]
    n_cls = len(labels["classes"])

    def run(outputs: Sequence[npt.NDArray[Any]]) -> Decoded:
        return postprocess(raw(outputs), [score] * n_cls, float(post["nms_iou_threshold"]), int(post["max_detections"]),
                           class_agnostic=bool(post["class_agnostic_nms"]))

    return run


def tflite_runner(model_path: Path) -> Runner:  # pragma: no cover - needs a LiteRT interpreter
    """Build a runner from a .tflite file using ai-edge-litert, else tensorflow.lite."""
    try:
        from ai_edge_litert.interpreter import Interpreter
    except ImportError:
        try:
            from tensorflow.lite import Interpreter
        except ImportError as exc:
            raise RuntimeError("no LiteRT interpreter: uv sync --extra export (Linux) or pip install ai-edge-litert") from exc
    interp = Interpreter(model_path=str(model_path))
    interp.allocate_tensors()
    inp = interp.get_input_details()[0]
    outs = interp.get_output_details()

    def run(x: npt.NDArray[Any]) -> list[npt.NDArray[Any]]:
        data = x
        if inp["dtype"] in (np.int8, np.uint8):
            scale, zp = inp["quantization"]
            data = np.clip(np.round(x / scale + zp), np.iinfo(inp["dtype"]).min, np.iinfo(inp["dtype"]).max)
        interp.set_tensor(inp["index"], data.astype(inp["dtype"]))
        interp.invoke()
        res = []
        for o in outs:
            v = interp.get_tensor(o["index"])
            if o["dtype"] in (np.int8, np.uint8) and o["quantization"][0]:
                v = o["quantization"][0] * (v.astype(np.float64) - o["quantization"][1])
            res.append(v)
        return res

    return run


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI over optional deps
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--float-model", type=Path, required=True)
    p.add_argument("--int8-model", type=Path, required=True)
    p.add_argument("--samples", type=Path, required=True, help=".npy file of preprocessed inputs, shape (N, H, W, 3)")
    p.add_argument("--min-cosine", type=float, default=DEFAULT_MIN_COSINE)
    p.add_argument("--min-agreement", type=float, default=DEFAULT_MIN_AGREEMENT)
    p.add_argument("--labels", type=Path, help="labels.json of the INT8 bundle -> also detection-level agreement")
    p.add_argument("--agreement-score", type=float, default=AGREEMENT_SCORE)
    p.add_argument("--report", type=Path)
    args = p.parse_args(argv)
    xs = np.load(args.samples)
    decode = None
    if args.labels:
        decode = labels_decoder(json.loads(args.labels.read_text(encoding="utf-8")), score=args.agreement_score)
    rep = compare(tflite_runner(args.float_model), tflite_runner(args.int8_model), (x[None, ...] for x in xs),
                  decode=decode, min_cosine=args.min_cosine, min_agreement=args.min_agreement)
    text = json.dumps(rep.to_dict(), indent=1)
    if args.report:
        args.report.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if rep.passed else 1


if __name__ == "__main__":
    sys.exit(main())
