"""Choose per-class score thresholds on the VALIDATION split (never on test).

    python thresholds.py --pred out/preds_val.jsonl --gt canonical.jsonl --splits splits.json \
        --split val --target-sensitivity 0.85 --out out/thresholds.json

Rule (technical, transparent, NOT clinical): image level, per class. Among candidate thresholds
(every distinct predicted score, plus 1.0), pick the HIGHEST threshold whose image-level
sensitivity on val is >= ``--target-sensitivity`` (screening favours sensitivity; PRD §3.3
hypothesis 0.85); if none reaches the target, pick the threshold with the best Youden J
(sens + spec - 1), ties -> higher threshold. A class with no positive val image gets 1.0
(effectively off) and a note.

The output has ``status: "set_on_val"`` -- export.py --release still refuses it
until a dentist reviews the operating point (``reviewed_by_drg``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from canonical import CLASS_NAMES, ImageAnnotation
from eval import load_predictions
from preprocess import split_records

STATUS = "set_on_val"


def image_scores(gt: Sequence[ImageAnnotation], preds: Mapping[str, Sequence[Any]], cls: str) -> tuple[list[float], list[bool]]:
    """Per val image: max predicted score of ``cls`` (0 if none) and whether it has a GT box of ``cls``."""
    scores, positive = [], []
    for rec in gt:
        s = [float(b.score or 0.0) for b in preds.get(rec.image, []) if b.cls == cls]
        scores.append(max(s, default=0.0))
        positive.append(any(b.cls == cls for b in rec.boxes))
    return scores, positive


def sens_spec(scores: Sequence[float], positive: Sequence[bool], t: float) -> tuple[float | None, float | None]:
    tp = sum(1 for s, p in zip(scores, positive, strict=True) if p and s >= t)
    fn = sum(1 for s, p in zip(scores, positive, strict=True) if p and s < t)
    tn = sum(1 for s, p in zip(scores, positive, strict=True) if not p and s < t)
    fp = sum(1 for s, p in zip(scores, positive, strict=True) if not p and s >= t)
    return (tp / (tp + fn) if tp + fn else None), (tn / (tn + fp) if tn + fp else None)


def choose(scores: Sequence[float], positive: Sequence[bool], target_sensitivity: float) -> dict[str, Any]:
    if not any(positive):
        return {"threshold": 1.0, "rule": "no_positive_val_images", "sensitivity": None, "specificity": None}
    candidates = sorted({*(s for s in scores if s > 0.0), 1.0}, reverse=True)
    for t in candidates:  # highest first
        sens, spec = sens_spec(scores, positive, t)
        if sens is not None and sens >= target_sensitivity:
            return {"threshold": t, "rule": f"highest_with_sensitivity>={target_sensitivity}", "sensitivity": sens, "specificity": spec}
    best: dict[str, Any] | None = None
    best_j = -2.0
    for t in candidates:
        sens, spec = sens_spec(scores, positive, t)
        j = (sens or 0.0) + (spec if spec is not None else 1.0) - 1.0
        if j > best_j:  # strict: ties keep the higher (earlier) threshold
            best_j, best = j, {"threshold": t, "rule": "max_youden_j (target not reachable)", "sensitivity": sens, "specificity": spec}
    assert best is not None
    return best


def select_thresholds(gt: Sequence[ImageAnnotation], preds: Mapping[str, Sequence[Any]], *, target_sensitivity: float,
                      source: str) -> dict[str, Any]:
    out: dict[str, Any] = {"status": STATUS, "source": source, "split": "val", "level": "image", "details": {}}
    for cls in CLASS_NAMES:
        res = choose(*image_scores(gt, preds, cls), target_sensitivity)
        out[cls] = res["threshold"]
        out["details"][cls] = res
    out["note"] = "Technical operating point on val; NOT reviewed by a dentist. Release requires reviewed_by_drg."
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pred", type=Path, required=True)
    p.add_argument("--gt", type=Path, required=True)
    p.add_argument("--splits", type=Path, required=True)
    p.add_argument("--split", default="val", choices=["val"], help="thresholds are only ever chosen on val")
    p.add_argument("--target-sensitivity", type=float, default=0.85)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    try:
        gt = split_records(args.gt, args.splits, args.split)
        preds = load_predictions(args.pred)
    except (ValueError, OSError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    source = f"val preds sha256 {hashlib.sha256(args.pred.read_bytes()).hexdigest()}; splits sha256 {hashlib.sha256(args.splits.read_bytes()).hexdigest()}"
    res = select_thresholds(gt, preds, target_sensitivity=args.target_sensitivity, source=source)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({c: res[c] for c in CLASS_NAMES} | {"status": res["status"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
