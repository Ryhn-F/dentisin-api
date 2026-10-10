"""Detector evaluation (pure numpy): per-class sensitivity / specificity / PPV / NPV, confusion
matrices, AP50/mAP50, image-level and box-level, subgroup breakdowns, and bootstrap 95% CIs
resampled over PATIENTS.

    python eval.py --pred preds.jsonl --split test --metadata meta.csv --report out/report.json

Definitions (also in README "Evaluation"):
- Classes are evaluated SEPARATELY (caries_permanent vs caries_primary, ADR 014) plus a
  class-agnostic pseudo-class ``any_caries`` whose IMAGE-level metrics answer "ada indikasi karies pada
  gigi?" (its box-level/AP numbers are class-agnostic localisation only). There are no severity metrics and
  no box-size subgroups: boxes are whole teeth and their size says nothing about severity.
- Operating point: a prediction counts if ``score >= threshold[class]``. Thresholds are
  PLACEHOLDER until set on a validation split (never on test) and reviewed.
- Image level: image positive for class c if it has >= 1 GT box of c; predicted positive if it
  has >= 1 thresholded prediction of c. TP/FP/FN/TN over images.
- Box level: within a class, predictions sorted by score (desc, stable); each is matched to the
  UNMATCHED GT box with the highest IoU; IoU >= 0.5 -> TP else FP; unmatched GT -> FN.
  Specificity/NPV are undefined at box level (no true negatives) and reported as null.
- AP50: same matching over ALL predictions (no threshold), all-point interpolated precision
  envelope (VOC2010+ style; not numerically identical to COCO's 101-point AP). mAP50 = mean AP
  over real classes with >= 1 GT box.
- Box confusion matrix (multi-class, thresholded): greedy one-to-one matching of GT and
  predictions of ANY class by descending IoU (>= 0.5); unmatched -> "background" row/column.
- Bootstrap: resample patients with replacement (falls back to images, with a warning, when no
  patient id is available); percentile 95% CI; undefined replicates are skipped (``n_valid``).
- Ratios with a zero denominator are null, never 0 or 1.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from canonical import CLASS_NAMES, AnnotationError, Box, ImageAnnotation, read_jsonl

REPORT_SCHEMA_VERSION = "1.0.0"
ANY = "any_caries"
EVAL_KEYS: tuple[str, ...] = (*CLASS_NAMES, ANY)
BACKGROUND = "background"
DEFAULT_SUBGROUPS = ("phone_model", "lighting", "source")  # dentition IS the class split now (ADR 014)
DISCLAIMER = (
    "Technical evaluation on the given split only. Not a clinical validation and not an accuracy "
    "claim (PRD §12, ADR 002). Screening output must say 'indikasi / perlu diperiksa'."
)
FloatArr = npt.NDArray[np.float64]
BoolArr = npt.NDArray[np.bool_]
IntArr = npt.NDArray[np.int64]


# ------------------------------------------------------------------------------- primitives


def iou(a: Box, b: Box) -> float:
    ix = min(a.x2, b.x2) - max(a.x1, b.x1)
    iy = min(a.y2, b.y2) - max(a.y1, b.y1)
    if ix <= 0 or iy <= 0:
        return 0.0
    inter = ix * iy
    union = a.area + b.area - inter
    return inter / union if union > 0 else 0.0


def ratio(num: float, den: float) -> float | None:
    return None if den <= 0 else num / den


def _score(b: Box) -> float:
    return 0.0 if b.score is None else float(b.score)


def match_class(gts: Sequence[Box], preds: Sequence[Box], iou_thr: float) -> tuple[list[bool], int]:
    """Greedy matching of ONE class. Returns (is_tp per pred in score-desc order, n_matched_gt)."""
    order = sorted(range(len(preds)), key=lambda i: -_score(preds[i]))  # stable for ties
    matched = [False] * len(gts)
    flags: list[bool] = []
    for pi in order:
        best, best_iou = -1, iou_thr
        for gi, g in enumerate(gts):
            if matched[gi]:
                continue
            v = iou(preds[pi], g)
            if v >= best_iou and (best == -1 or v > best_iou):
                best, best_iou = gi, v
        if best >= 0:
            matched[best] = True
            flags.append(True)
        else:
            flags.append(False)
    return flags, sum(matched)


def average_precision(scores: FloatArr, is_tp: BoolArr, weights: FloatArr, n_gt: float) -> float | None:
    """All-point interpolated AP. ``weights`` = per-prediction bootstrap multiplicity (1 for point estimate).
    Inputs must already be sorted by descending score."""
    if n_gt <= 0:
        return None
    if scores.size == 0:
        return 0.0
    tp = np.cumsum(weights * is_tp)
    fp = np.cumsum(weights * ~is_tp)
    recall = tp / n_gt
    denom = tp + fp
    precision = np.divide(tp, denom, out=np.zeros_like(tp), where=denom > 0)
    mrec = np.concatenate(([0.0], recall, [recall[-1]]))
    mpre = np.concatenate(([0.0], precision, [0.0]))
    mpre = np.maximum.accumulate(mpre[::-1])[::-1]
    idx = np.nonzero(mrec[1:] != mrec[:-1])[0]
    return float(np.sum((mrec[idx + 1] - mrec[idx]) * mpre[idx + 1]))


def _with_cls(b: Box, cls: str) -> Box:
    return Box(cls, b.x1, b.y1, b.x2, b.y2, b.score)


def _select(boxes: Sequence[Box], key: str) -> list[Box]:
    return [_with_cls(b, ANY) for b in boxes] if key == ANY else [b for b in boxes if b.cls == key]


# ------------------------------------------------------------------------- per-image tables


@dataclass
class ClassTable:
    """Per-image vectors for one evaluation key (class or any_caries)."""

    gt_pos: BoolArr
    pred_pos: BoolArr
    box_tp: FloatArr
    box_fp: FloatArr
    box_fn: FloatArr
    n_gt: FloatArr
    ap_scores: FloatArr  # all predictions, sorted by descending score
    ap_tp: BoolArr
    ap_img: IntArr


@dataclass
class EvalTables:
    images: list[str]
    units: list[str]  # bootstrap unit (patient id or image) per image
    unit_kind: str
    per_key: dict[str, ClassTable]
    confusion: IntArr  # (C+1, C+1), rows GT, cols pred, last = background
    n_pred_boxes: dict[str, int] = field(default_factory=dict)


def build_tables(
    gt: Sequence[ImageAnnotation],
    preds: Mapping[str, Sequence[Box]],
    thresholds: Mapping[str, float],
    *,
    iou_thr: float = 0.5,
    patient_of: Mapping[str, str | None] | None = None,
) -> EvalTables:
    n = len(gt)
    images = [g.image for g in gt]
    patient_of = patient_of or {}
    pids = [patient_of.get(g.image) or g.patient_id for g in gt]
    if all(pids):
        units, unit_kind = [str(p) for p in pids], "patient"
    else:
        units, unit_kind = list(images), "image"

    def thr_ok(b: Box) -> bool:
        return _score(b) >= thresholds[b.cls]

    per_key: dict[str, ClassTable] = {}
    n_pred_boxes: dict[str, int] = {}
    for key in EVAL_KEYS:
        gt_pos = np.zeros(n, dtype=bool)
        pred_pos = np.zeros(n, dtype=bool)
        tp = np.zeros(n)
        fp = np.zeros(n)
        fn = np.zeros(n)
        ngt = np.zeros(n)
        s_all: list[float] = []
        t_all: list[bool] = []
        i_all: list[int] = []
        total_pred = 0
        for i, g in enumerate(gt):
            gboxes = _select(g.boxes, key)
            all_preds = list(preds.get(g.image, []))
            kept = _select([b for b in all_preds if thr_ok(b)], key)
            every = _select(all_preds, key)
            total_pred += len(every)
            gt_pos[i] = bool(gboxes)
            pred_pos[i] = bool(kept)
            ngt[i] = len(gboxes)
            flags, m = match_class(gboxes, kept, iou_thr)
            tp[i], fp[i], fn[i] = sum(flags), len(flags) - sum(flags), len(gboxes) - m
            ap_flags, _ = match_class(gboxes, every, iou_thr)
            ordered = sorted(every, key=lambda b: -_score(b))
            s_all.extend(_score(b) for b in ordered)
            t_all.extend(ap_flags)
            i_all.extend([i] * len(ordered))
        order = np.argsort(-np.asarray(s_all, dtype=float), kind="stable")
        per_key[key] = ClassTable(
            gt_pos, pred_pos, tp, fp, fn, ngt,
            np.asarray(s_all, dtype=float)[order],
            np.asarray(t_all, dtype=bool)[order],
            np.asarray(i_all, dtype=np.int64)[order],
        )
        n_pred_boxes[key] = total_pred

    labels = [*CLASS_NAMES, BACKGROUND]
    cm = np.zeros((len(labels), len(labels)), dtype=np.int64)
    for g in gt:
        kept = [b for b in preds.get(g.image, []) if thr_ok(b)]
        pairs = sorted(
            ((iou(gb, pb), gi, pi) for gi, gb in enumerate(g.boxes) for pi, pb in enumerate(kept)),
            key=lambda t: (-t[0], t[1], t[2]),
        )
        gm: set[int] = set()
        pm: set[int] = set()
        for v, gi, pi in pairs:
            if v < iou_thr:
                break
            if gi in gm or pi in pm:
                continue
            gm.add(gi)
            pm.add(pi)
            cm[labels.index(g.boxes[gi].cls), labels.index(kept[pi].cls)] += 1
        for gi, gb in enumerate(g.boxes):
            if gi not in gm:
                cm[labels.index(gb.cls), -1] += 1
        for pi, pb in enumerate(kept):
            if pi not in pm:
                cm[-1, labels.index(pb.cls)] += 1
    return EvalTables(images, units, unit_kind, per_key, cm, n_pred_boxes)


# ----------------------------------------------------------------------------- metric blocks


def _image_metrics(t: ClassTable, w: FloatArr) -> dict[str, Any]:
    tp = float(np.sum(w * (t.gt_pos & t.pred_pos)))
    fn = float(np.sum(w * (t.gt_pos & ~t.pred_pos)))
    fp = float(np.sum(w * (~t.gt_pos & t.pred_pos)))
    tn = float(np.sum(w * (~t.gt_pos & ~t.pred_pos)))
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "sensitivity": ratio(tp, tp + fn), "specificity": ratio(tn, tn + fp),
        "ppv": ratio(tp, tp + fp), "npv": ratio(tn, tn + fn),
    }


def _box_metrics(t: ClassTable, w: FloatArr) -> dict[str, Any]:
    tp, fp, fn = float(np.sum(w * t.box_tp)), float(np.sum(w * t.box_fp)), float(np.sum(w * t.box_fn))
    return {"tp": tp, "fp": fp, "fn": fn, "sensitivity": ratio(tp, tp + fn), "ppv": ratio(tp, tp + fp),
            "specificity": None, "npv": None}


def _ap(t: ClassTable, w: FloatArr) -> float | None:
    return average_precision(t.ap_scores, t.ap_tp, w[t.ap_img], float(np.sum(w * t.n_gt)))


def _map(per_key_ap: Mapping[str, float | None]) -> float | None:
    vals = [v for k, v in per_key_ap.items() if k != ANY and v is not None]
    return float(np.mean(vals)) if vals else None


def _ints(d: dict[str, Any]) -> dict[str, Any]:
    return {k: (int(v) if k in ("tp", "fp", "fn", "tn") else v) for k, v in d.items()}


def point_metrics(tables: EvalTables, idx: IntArr) -> dict[str, Any]:
    """Point estimates restricted to image indices ``idx``."""
    n = len(tables.images)
    w = np.zeros(n)
    w[idx] = 1.0
    per_class: dict[str, Any] = {}
    aps: dict[str, float | None] = {}
    for key, t in tables.per_key.items():
        aps[key] = _ap(t, w)
        per_class[key] = {
            "n_gt_boxes": int(np.sum(w * t.n_gt)),
            "n_positive_images": int(np.sum(w * t.gt_pos)),
            "image_level": _ints(_image_metrics(t, w)),
            "box_level": _ints(_box_metrics(t, w)),
            "ap50": aps[key],
        }
    units = {tables.units[i] for i in idx.tolist()}
    return {"n_images": int(idx.size), "n_units": len(units), "per_class": per_class, "map50": _map(aps)}


def bootstrap_ci(
    tables: EvalTables, idx: IntArr, n_boot: int, seed: int, alpha: float = 0.05
) -> dict[str, Any]:
    """Percentile CIs from resampling bootstrap units (patients) within ``idx``."""
    unit_names = sorted({tables.units[i] for i in idx.tolist()})
    if n_boot <= 0 or not unit_names:
        return {}
    unit_index = {u: k for k, u in enumerate(unit_names)}
    img_unit = np.array([unit_index[tables.units[i]] for i in idx.tolist()], dtype=np.int64)
    rng = np.random.default_rng(seed)
    n = len(tables.images)
    samples: dict[str, list[float | None]] = {}

    def push(name: str, v: float | None) -> None:
        samples.setdefault(name, []).append(v)

    for _ in range(n_boot):
        draw = rng.integers(0, len(unit_names), size=len(unit_names))
        counts = np.bincount(draw, minlength=len(unit_names)).astype(float)
        w = np.zeros(n)
        w[idx] = counts[img_unit]
        aps: dict[str, float | None] = {}
        for key, t in tables.per_key.items():
            im = _image_metrics(t, w)
            bx = _box_metrics(t, w)
            for m in ("sensitivity", "specificity", "ppv", "npv"):
                push(f"{key}/image_level/{m}", im[m])
            for m in ("sensitivity", "ppv"):
                push(f"{key}/box_level/{m}", bx[m])
            aps[key] = _ap(t, w)
            push(f"{key}/ap50", aps[key])
        push("map50", _map(aps))
    lo_q, hi_q = 100 * alpha / 2, 100 * (1 - alpha / 2)
    out: dict[str, Any] = {}
    for name, vals in samples.items():
        valid = np.array([v for v in vals if v is not None], dtype=float)
        if valid.size == 0:
            out[name] = {"lo": None, "hi": None, "n_valid": 0}
        else:
            out[name] = {"lo": float(np.percentile(valid, lo_q)), "hi": float(np.percentile(valid, hi_q)),
                         "n_valid": int(valid.size)}
    return out


def attach_ci(block: dict[str, Any], ci: Mapping[str, Any]) -> None:
    for key, pc in block["per_class"].items():
        for level, metrics in (("image_level", ("sensitivity", "specificity", "ppv", "npv")),
                               ("box_level", ("sensitivity", "ppv"))):
            pc[level]["ci95"] = {m: ci.get(f"{key}/{level}/{m}") for m in metrics} if ci else None
        pc["ap50_ci95"] = ci.get(f"{key}/ap50") if ci else None
    block["map50_ci95"] = ci.get("map50") if ci else None


def evaluate(
    gt: Sequence[ImageAnnotation],
    preds: Mapping[str, Sequence[Box]],
    thresholds: Mapping[str, float],
    *,
    metadata: Mapping[str, Mapping[str, str]] | None = None,
    subgroups: Sequence[str] = DEFAULT_SUBGROUPS,
    iou_thr: float = 0.5,
    n_boot: int = 1000,
    seed: int = 0,
    subgroup_bootstrap: bool = True,
) -> dict[str, Any]:
    """Compute the ``overall`` / ``subgroups`` / ``confusion`` part of the report."""
    metadata = metadata or {}
    patient_of = {img: (row.get("patient_id") or None) for img, row in metadata.items()}
    tables = build_tables(gt, preds, thresholds, iou_thr=iou_thr, patient_of=patient_of)
    all_idx = np.arange(len(gt), dtype=np.int64)
    overall = point_metrics(tables, all_idx)
    attach_ci(overall, bootstrap_ci(tables, all_idx, n_boot, seed))
    overall["box_confusion_matrix"] = {
        "labels": [*CLASS_NAMES, BACKGROUND],
        "rows": "ground_truth",
        "cols": "prediction",
        "matrix": tables.confusion.tolist(),
    }
    columns = set().union(*(row.keys() for row in metadata.values())) if metadata else set()
    sub: dict[str, Any] = {}
    for col in subgroups:
        if col == "source":
            values = [metadata.get(g.image, {}).get("source") or g.source or "unknown" for g in gt]
        elif col not in columns:
            sub[col] = {"status": "column_missing"}
            continue
        else:
            values = [metadata.get(g.image, {}).get(col) or "unknown" for g in gt]
        groups: dict[str, Any] = {}
        for value in sorted(set(values)):
            idx = np.array([i for i, v in enumerate(values) if v == value], dtype=np.int64)
            block = point_metrics(tables, idx)
            attach_ci(block, bootstrap_ci(tables, idx, n_boot if subgroup_bootstrap else 0, seed))
            groups[value] = block
        sub[col] = {"status": "ok", "groups": groups}
    warnings: list[str] = []
    if tables.unit_kind != "patient":
        warnings.append("patient_id missing for some images: bootstrap resamples IMAGES; CIs are too narrow if a patient has several images")
    missing_pred = [g.image for g in gt if g.image not in preds]
    if missing_pred:
        warnings.append(f"{len(missing_pred)} GT images have no prediction entry (treated as no detections)")
    extra = sorted(set(preds) - {g.image for g in gt})
    if extra:
        warnings.append(f"{len(extra)} prediction entries are not in the evaluated GT set (ignored)")
    return {
        "counts": {
            "n_images": len(gt),
            "n_units": len(set(tables.units)),
            "bootstrap_unit": tables.unit_kind,
            "n_pred_boxes": tables.n_pred_boxes,
        },
        "overall": overall,
        "subgroups": sub,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------------------- I/O


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_predictions(path: Path) -> dict[str, list[Box]]:
    out: dict[str, list[Box]] = {}
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            d = json.loads(line)
            boxes = [Box.from_dict(b) for b in d.get("boxes", [])]
            for b in boxes:
                if b.score is None:
                    raise AnnotationError(f"{path}:{lineno}: prediction without score")
                if b.cls not in CLASS_NAMES:
                    raise AnnotationError(f"{path}:{lineno}: unknown class {b.cls!r}")
            if d["image"] in out:
                raise AnnotationError(f"{path}:{lineno}: duplicate image {d['image']!r}")
            out[str(d["image"])] = boxes
    return out


def load_metadata(path: Path) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None or "image" not in reader.fieldnames:
            raise AnnotationError(f"{path}: metadata CSV needs an 'image' column")
        return {row["image"]: {k: (v or "") for k, v in row.items() if k != "image"} for row in reader}


def resolve_thresholds(default: float | None, path: Path | None) -> tuple[dict[str, float], str]:
    if path is not None:
        data = json.loads(path.read_text(encoding="utf-8"))
        missing = [c for c in CLASS_NAMES if data.get(c) is None]
        if missing:
            raise AnnotationError(f"thresholds file has null/missing classes: {missing}")
        return {c: float(data[c]) for c in CLASS_NAMES}, str(data.get("status", "set"))
    value = 0.5 if default is None else default
    return dict.fromkeys(CLASS_NAMES, value), "PLACEHOLDER"


def build_report(
    *,
    gt_path: Path,
    pred_path: Path,
    split: str | None,
    splits_path: Path | None,
    metadata_path: Path | None,
    thresholds: Mapping[str, float],
    threshold_status: str,
    iou_thr: float,
    n_boot: int,
    seed: int,
    subgroups: Sequence[str],
    candidate: str | None = None,
    model_version: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    gt = read_jsonl(gt_path)
    if split is not None:
        if splits_path is None:
            raise AnnotationError("--split needs --splits")
        assign = json.loads(splits_path.read_text(encoding="utf-8"))["images"]
        gt = [g for g in gt if assign.get(g.image) == split]
        if not gt:
            raise AnnotationError(f"no images in split {split!r}")
    preds = load_predictions(pred_path)
    metadata = load_metadata(metadata_path) if metadata_path else {}
    body = evaluate(gt, preds, thresholds, metadata=metadata, subgroups=subgroups, iou_thr=iou_thr, n_boot=n_boot, seed=seed)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "tool": "ml/detection/eval.py",
        "created_at": (now or datetime.now(UTC)).isoformat(),
        "inputs": {
            "gt_path": gt_path.as_posix(), "gt_sha256": sha256_file(gt_path),
            "predictions_path": pred_path.as_posix(), "predictions_sha256": sha256_file(pred_path),
            "split": split,
            "splits_path": splits_path.as_posix() if splits_path else None,
            "split_sha256": sha256_file(splits_path) if splits_path else None,
            "metadata_path": metadata_path.as_posix() if metadata_path else None,
            "metadata_sha256": sha256_file(metadata_path) if metadata_path else None,
            "candidate": candidate,
            "model_version": model_version,
        },
        "settings": {
            "classes": list(CLASS_NAMES),
            "iou_threshold": iou_thr,
            "score_thresholds": dict(thresholds),
            "score_threshold_status": threshold_status,
            "matching": "greedy per class, score-desc, best-IoU unmatched GT",
            "ap_method": "all_point_interpolation",
            "bootstrap": {"n": n_boot, "seed": seed, "ci": 0.95, "method": "percentile"},
        },
        **body,
        "disclaimer": DISCLAIMER,
    }


def main(argv: list[str] | None = None) -> int:
    here = Path(__file__).resolve().parent
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--gt", type=Path, default=here.parent / "data/processed/ahmed2025/canonical.jsonl")
    p.add_argument("--pred", type=Path, required=True, help="JSONL {image, boxes:[{cls,x1,y1,x2,y2,score}]}")
    p.add_argument("--splits", type=Path, default=here.parent / "data/processed/ahmed2025/splits/splits.json")
    p.add_argument("--split", choices=["train", "val", "test"])
    p.add_argument("--metadata", type=Path, help="CSV: image,patient_id,phone_model,lighting,source")
    p.add_argument("--subgroups", default=",".join(DEFAULT_SUBGROUPS))
    p.add_argument("--score-threshold", type=float, help="same threshold for all classes (PLACEHOLDER)")
    p.add_argument("--thresholds", type=Path, help="JSON {class: threshold, status}")
    p.add_argument("--iou", type=float, default=0.5)
    p.add_argument("--bootstrap", type=int, default=1000)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--candidate")
    p.add_argument("--model-version")
    p.add_argument("--report", type=Path, required=True)
    args = p.parse_args(argv)
    try:
        thresholds, status = resolve_thresholds(args.score_threshold, args.thresholds)
        report = build_report(
            gt_path=args.gt, pred_path=args.pred, split=args.split,
            splits_path=args.splits if args.split else None, metadata_path=args.metadata,
            thresholds=thresholds, threshold_status=status, iou_thr=args.iou, n_boot=args.bootstrap,
            seed=args.seed, subgroups=[s for s in args.subgroups.split(",") if s],
            candidate=args.candidate, model_version=args.model_version,
        )
    except (AnnotationError, OSError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(args.report), "map50": report["overall"]["map50"], "warnings": report["warnings"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
