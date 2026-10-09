"""Reference implementation of the detector output DECODE procedures described in DECODE_SPEC.md.

The TypeScript app (app/src/...) must reproduce these functions; ``tests/decode_vectors.json``
holds input/expected-output pairs for port parity. Pure numpy, float64, deterministic.

All decoders return boxes in MODEL-INPUT pixel coordinates (x1, y1, x2, y2), class ids
(0-based, canonical order) and scores in [0, 1]. Then :func:`postprocess` applies per-class
score thresholds + NMS, and :func:`unletterbox` / :func:`unstretch` map back to the photo.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

FloatArr = npt.NDArray[np.float64]
IntArr = npt.NDArray[np.int64]

SPEC_VERSION = "1.0.0"


@dataclass(frozen=True)
class Decoded:
    boxes: FloatArr  # (N, 4) x1, y1, x2, y2 in input pixels
    classes: IntArr  # (N,)
    scores: FloatArr  # (N,)


def dequantize(q: npt.ArrayLike, scale: float, zero_point: int) -> FloatArr:
    """real = scale * (q - zero_point)  (TFLite affine quantization)."""
    return scale * (np.asarray(q, dtype=np.float64) - float(zero_point))


def quantize_input(x: npt.ArrayLike, scale: float, zero_point: int, dtype: str) -> npt.NDArray[Any]:
    """q = clamp(round_half_away_from_zero(x / scale) + zero_point) for uint8/int8 inputs."""
    v = np.asarray(x, dtype=np.float64) / scale
    r = np.sign(v) * np.floor(np.abs(v) + 0.5) + zero_point
    lo, hi = (0, 255) if dtype == "uint8" else (-128, 127)
    out: npt.NDArray[Any] = np.clip(r, lo, hi).astype(np.uint8 if dtype == "uint8" else np.int8)
    return out


def sigmoid(x: FloatArr) -> FloatArr:
    out: FloatArr = 1.0 / (1.0 + np.exp(-x))
    return out


# --------------------------------------------------------------------------------- geometry


@dataclass(frozen=True)
class Letterbox:
    scale: float
    pad_x: float
    pad_y: float


def letterbox_params(src_w: int, src_h: int, dst_w: int, dst_h: int, align: str = "top_left") -> Letterbox:
    """Uniform scale r = min(dst_w/src_w, dst_h/src_h); image placed top-left (YOLOX) or centered."""
    r = min(dst_w / src_w, dst_h / src_h)
    if align == "top_left":
        return Letterbox(r, 0.0, 0.0)
    if align == "center":
        return Letterbox(r, (dst_w - src_w * r) / 2.0, (dst_h - src_h * r) / 2.0)
    raise ValueError(f"unknown align {align!r}")


def unletterbox(boxes: FloatArr, lb: Letterbox, src_w: int, src_h: int) -> FloatArr:
    out = np.array(boxes, dtype=np.float64, copy=True).reshape(-1, 4)
    out[:, [0, 2]] = (out[:, [0, 2]] - lb.pad_x) / lb.scale
    out[:, [1, 3]] = (out[:, [1, 3]] - lb.pad_y) / lb.scale
    out[:, [0, 2]] = np.clip(out[:, [0, 2]], 0, src_w)
    out[:, [1, 3]] = np.clip(out[:, [1, 3]], 0, src_h)
    return out


def unstretch(boxes: FloatArr, in_w: int, in_h: int, src_w: int, src_h: int) -> FloatArr:
    """Inverse of a plain (aspect-ratio-changing) resize to (in_w, in_h)."""
    out = np.array(boxes, dtype=np.float64, copy=True).reshape(-1, 4)
    out[:, [0, 2]] *= src_w / in_w
    out[:, [1, 3]] *= src_h / in_h
    out[:, [0, 2]] = np.clip(out[:, [0, 2]], 0, src_w)
    out[:, [1, 3]] = np.clip(out[:, [1, 3]], 0, src_h)
    return out


def box_iou_matrix(a: FloatArr, b: FloatArr) -> FloatArr:
    a = a.reshape(-1, 4)
    b = b.reshape(-1, 4)
    ix = np.clip(np.minimum(a[:, None, 2], b[None, :, 2]) - np.maximum(a[:, None, 0], b[None, :, 0]), 0, None)
    iy = np.clip(np.minimum(a[:, None, 3], b[None, :, 3]) - np.maximum(a[:, None, 1], b[None, :, 1]), 0, None)
    inter = ix * iy
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    union = area_a[:, None] + area_b[None, :] - inter
    res: FloatArr = np.divide(inter, union, out=np.zeros_like(inter), where=union > 0)
    return res


def nms(boxes: FloatArr, scores: FloatArr, iou_threshold: float) -> IntArr:
    """Greedy hard NMS. Order: score desc, ties broken by lower index. Suppress if IoU > threshold."""
    order = sorted(range(len(scores)), key=lambda i: (-float(scores[i]), i))
    keep: list[int] = []
    suppressed = np.zeros(len(scores), dtype=bool)
    ious = box_iou_matrix(boxes, boxes) if len(scores) else np.zeros((0, 0))
    for i in order:
        if suppressed[i]:
            continue
        keep.append(i)
        suppressed |= ious[i] > iou_threshold
    return np.asarray(keep, dtype=np.int64)


# --------------------------------------------------------------------------------- decoders


def yolox_grid(input_w: int, input_h: int, strides: Sequence[int]) -> tuple[FloatArr, FloatArr]:
    """Grid (gx, gy) and stride per output row, in YOLOX order: stride-major, then y, then x."""
    grids: list[FloatArr] = []
    sts: list[FloatArr] = []
    for s in strides:
        hs, ws = input_h // s, input_w // s
        yv, xv = np.meshgrid(np.arange(hs), np.arange(ws), indexing="ij")
        grids.append(np.stack([xv.reshape(-1), yv.reshape(-1)], axis=1).astype(np.float64))
        sts.append(np.full(hs * ws, float(s)))
    return np.concatenate(grids), np.concatenate(sts)


def decode_yolox(
    raw: npt.ArrayLike, input_w: int, input_h: int, strides: Sequence[int] = (8, 16, 32), *, sigmoid_applied: bool = True
) -> Decoded:
    """Raw YOLOX head output (``decode_in_inference=False``), shape (N, 5 + C) or (1, N, 5 + C):
    [tx, ty, tw, th, obj, cls_0..cls_{C-1}]. cx = (tx + gx) * s, w = exp(tw) * s;
    score = obj * max_c(cls_c), class = argmax_c (first max wins)."""
    arr = np.asarray(raw, dtype=np.float64)
    arr = arr.reshape(-1, arr.shape[-1])
    grid, st = yolox_grid(input_w, input_h, strides)
    if arr.shape[0] != grid.shape[0]:
        raise ValueError(f"expected {grid.shape[0]} rows for {input_w}x{input_h} strides {list(strides)}, got {arr.shape[0]}")
    obj = arr[:, 4]
    cls = arr[:, 5:]
    if not sigmoid_applied:
        obj, cls = sigmoid(obj), sigmoid(cls)
    cx = (arr[:, 0] + grid[:, 0]) * st
    cy = (arr[:, 1] + grid[:, 1]) * st
    w = np.exp(arr[:, 2]) * st
    h = np.exp(arr[:, 3]) * st
    cid = np.argmax(cls, axis=1).astype(np.int64)
    score = obj * cls[np.arange(cls.shape[0]), cid]
    boxes = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1)
    return Decoded(boxes, cid, score)


def decode_ssd_raw(
    raw_boxes: npt.ArrayLike,
    raw_scores: npt.ArrayLike,
    anchors: npt.ArrayLike,
    input_w: int,
    input_h: int,
    *,
    scales: Sequence[float] = (10.0, 10.0, 5.0, 5.0),
    box_order: str = "yxhw",
    score_activation: str = "sigmoid",
    background_class: bool = False,
) -> Decoded:
    """SSD with anchors [y_center, x_center, h, w] (normalised). ``raw_boxes`` per anchor in
    ``box_order`` ("yxhw" = [ty, tx, th, tw] TFLite convention, or "xywh"); ``scales`` are
    (y_scale, x_scale, h_scale, w_scale). If ``background_class``, column 0 is dropped."""
    rb = np.asarray(raw_boxes, dtype=np.float64).reshape(-1, 4)
    rs = np.asarray(raw_scores, dtype=np.float64)
    rs = rs.reshape(rb.shape[0], -1)
    an = np.asarray(anchors, dtype=np.float64).reshape(-1, 4)
    if an.shape[0] != rb.shape[0]:
        raise ValueError(f"{an.shape[0]} anchors for {rb.shape[0]} boxes")
    if box_order == "yxhw":
        ty, tx, th, tw = rb.T
    elif box_order == "xywh":
        tx, ty, tw, th = rb.T
    else:
        raise ValueError(f"unknown box_order {box_order!r}")
    ys, xs, hs, ws = scales
    yc = ty / ys * an[:, 2] + an[:, 0]
    xc = tx / xs * an[:, 3] + an[:, 1]
    h = np.exp(th / hs) * an[:, 2]
    w = np.exp(tw / ws) * an[:, 3]
    if score_activation == "sigmoid":
        probs = sigmoid(rs)
    elif score_activation == "softmax":
        e = np.exp(rs - rs.max(axis=1, keepdims=True))
        probs = e / e.sum(axis=1, keepdims=True)
    elif score_activation == "none":
        probs = rs
    else:
        raise ValueError(f"unknown score_activation {score_activation!r}")
    if background_class:
        probs = probs[:, 1:]
    cid = np.argmax(probs, axis=1).astype(np.int64)
    score = probs[np.arange(probs.shape[0]), cid]
    boxes = np.stack([(xc - w / 2) * input_w, (yc - h / 2) * input_h, (xc + w / 2) * input_w, (yc + h / 2) * input_h], axis=1)
    return Decoded(boxes, cid, score)


def decode_ssd_postprocessed(
    boxes: npt.ArrayLike, classes: npt.ArrayLike, scores: npt.ArrayLike, count: float, input_w: int, input_h: int,
    *, class_id_offset: int = 0,
) -> Decoded:
    """TFLite_Detection_PostProcess outputs: boxes [ymin, xmin, ymax, xmax] normalised, classes
    (float ids), scores, count. NMS already applied by the model; :func:`postprocess` re-applies
    thresholds (and an idempotent NMS)."""
    n = int(count)
    b = np.asarray(boxes, dtype=np.float64).reshape(-1, 4)[:n]
    c = np.asarray(classes, dtype=np.float64).reshape(-1)[:n]
    s = np.asarray(scores, dtype=np.float64).reshape(-1)[:n]
    out = np.stack([b[:, 1] * input_w, b[:, 0] * input_h, b[:, 3] * input_w, b[:, 2] * input_h], axis=1)
    return Decoded(out, (np.rint(c).astype(np.int64) - class_id_offset), s)


def decode_detr(boxes_cxcywh: npt.ArrayLike, logits: npt.ArrayLike, input_w: int, input_h: int) -> Decoded:
    """DETR-style (RF-DETR): boxes normalised [cx, cy, w, h]; per-class sigmoid logits, no background."""
    b = np.asarray(boxes_cxcywh, dtype=np.float64).reshape(-1, 4)
    probs = sigmoid(np.asarray(logits, dtype=np.float64).reshape(b.shape[0], -1))
    cid = np.argmax(probs, axis=1).astype(np.int64)
    score = probs[np.arange(probs.shape[0]), cid]
    out = np.stack([(b[:, 0] - b[:, 2] / 2) * input_w, (b[:, 1] - b[:, 3] / 2) * input_h,
                    (b[:, 0] + b[:, 2] / 2) * input_w, (b[:, 1] + b[:, 3] / 2) * input_h], axis=1)
    return Decoded(out, cid, score)


def postprocess(
    d: Decoded,
    score_thresholds: Sequence[float],
    nms_iou: float,
    max_detections: int,
    *,
    class_agnostic: bool = False,
) -> Decoded:
    """1) drop invalid class ids, 2) keep score >= threshold[class], 3) NMS per class (or agnostic),
    4) sort by score desc (ties: lower original index), 5) truncate to ``max_detections``."""
    num_classes = len(score_thresholds)
    valid = (d.classes >= 0) & (d.classes < num_classes)
    thr = np.asarray(score_thresholds, dtype=np.float64)
    keep_mask = valid.copy()
    keep_mask[valid] = d.scores[valid] >= thr[d.classes[valid]]
    idx = np.nonzero(keep_mask)[0]
    kept: list[int] = []
    groups = [idx] if class_agnostic else [idx[d.classes[idx] == c] for c in range(num_classes)]
    for g in groups:
        if g.size:
            k = nms(d.boxes[g], d.scores[g], nms_iou)
            kept.extend(int(i) for i in g[k])
    kept.sort(key=lambda i: (-float(d.scores[i]), i))
    sel = np.asarray(kept[:max_detections], dtype=np.int64)
    return Decoded(d.boxes[sel].reshape(-1, 4), d.classes[sel], d.scores[sel])


def decode_from_spec(outputs: Mapping[str, npt.ArrayLike], spec: Mapping[str, Any], input_w: int, input_h: int) -> Decoded:
    """Dispatch on ``labels.json['output']['decode']['type']``. ``outputs`` keyed by tensor role."""
    t = spec["type"]
    if t == "yolox_raw":
        return decode_yolox(outputs["raw"], input_w, input_h, spec["strides"], sigmoid_applied=spec["sigmoid_applied"])
    if t == "ssd_raw_anchors":
        return decode_ssd_raw(outputs["raw_boxes"], outputs["raw_scores"], spec["anchors"], input_w, input_h,
                              scales=spec["scales"], box_order=spec["box_order"],
                              score_activation=spec["score_activation"], background_class=spec["background_class"])
    if t == "ssd_postprocessed":
        return decode_ssd_postprocessed(outputs["boxes"], outputs["classes"], outputs["scores"],
                                        float(np.asarray(outputs["count"]).reshape(-1)[0]), input_w, input_h,
                                        class_id_offset=spec["class_id_offset"])
    if t == "detr_raw":
        return decode_detr(outputs["boxes"], outputs["logits"], input_w, input_h)
    raise ValueError(f"unknown decode type {t!r}")
