"""Per-split statistics + leakage checks for a ``splits.json`` and a groups CSV (e.g. from ahmed2025_groups.py).

Checks (all must be 0 for a usable split):
- ``groups_in_multiple_splits``  -- heuristic patient groups spread over splits (make_splits guarantees 0);
- ``records_in_multiple_splits`` -- raw source record ids (column ``record_id``, if present) spread over splits;
- ``cross_split_pairs``          -- optional image pairs that must be together (exact/near duplicates found
  elsewhere, e.g. by CRC or dHash), given as a JSON list of ``[image_a, image_b]``.

Output: JSON with per-split counts by any categorical column (subset/view/device), positives, boxes per
source class, ``label_defect`` images, and the leakage counters. Stdlib only.

    python split_stats.py --splits ../splits/ahmed2025/splits.json --groups-csv ../processed/ahmed2025/groups.csv \
        --split-input ../processed/ahmed2025/split_input.jsonl --out ../splits/ahmed2025/split_stats.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

CATEGORICAL = ("subset", "view", "device")


def per_split_stats(
    assignments: Mapping[str, str], rows: Mapping[str, Mapping[str, str]], boxes: Mapping[str, Sequence[str]]
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for split in sorted(set(assignments.values())):
        imgs = [i for i, s in assignments.items() if s == split]
        st: dict[str, Any] = {
            "images": len(imgs),
            "groups": len({rows[i]["patient_id"] for i in imgs if i in rows}),
            "images_with_boxes": sum(1 for i in imgs if boxes.get(i)),
            "boxes_by_source_class": dict(sorted(Counter(b for i in imgs for b in boxes.get(i, ())).items())),
            "images_with_label_defects": sum(1 for i in imgs if int(rows.get(i, {}).get("label_defect") or 0) > 0),
        }
        st["positive_fraction"] = round(st["images_with_boxes"] / len(imgs), 4) if imgs else 0.0
        for col in CATEGORICAL:
            vals = Counter(rows[i].get(col) or "-" for i in imgs if i in rows)
            if any(v != "-" for v in vals):
                st[f"by_{col}"] = dict(sorted(vals.items()))
        out[split] = st
    return out


def leakage(
    assignments: Mapping[str, str], rows: Mapping[str, Mapping[str, str]], pairs: Iterable[Sequence[str]] = ()
) -> dict[str, Any]:
    by_group: dict[str, set[str]] = defaultdict(set)
    by_record: dict[str, set[str]] = defaultdict(set)
    for img, split in assignments.items():
        r = rows.get(img, {})
        if r.get("patient_id"):
            by_group[r["patient_id"]].add(split)
        if r.get("record_id"):
            by_record[r["record_id"]].add(split)
    pair_list = [tuple(p) for p in pairs]
    cross = [p for p in pair_list if assignments.get(p[0]) != assignments.get(p[1])]
    return {
        "images_without_group_row": sum(1 for i in assignments if i not in rows),
        "groups_in_multiple_splits": sum(1 for v in by_group.values() if len(v) > 1),
        "records_in_multiple_splits": sum(1 for v in by_record.values() if len(v) > 1),
        "pairs_checked": len(pair_list),
        "cross_split_pairs": len(cross),
        "cross_split_pair_examples": [list(p) for p in cross[:10]],
    }


def load_rows(path: Path) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return {r["image"]: r for r in csv.DictReader(fh)}


def load_boxes(path: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rec = json.loads(line)
                out[rec["image"]] = [str(b.get("src") or b.get("cls") or "?") for b in rec.get("boxes", [])]
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--splits", type=Path, required=True)
    p.add_argument("--groups-csv", type=Path, required=True)
    p.add_argument("--split-input", type=Path, help="JSONL with image + boxes (split_input.jsonl or canonical.jsonl)")
    p.add_argument("--pairs", type=Path, help="JSON list of [image_a, image_b] that must share a split")
    p.add_argument("--out", type=Path)
    args = p.parse_args(argv)
    assignments: dict[str, str] = json.loads(args.splits.read_text(encoding="utf-8"))["images"]
    rows = load_rows(args.groups_csv)
    boxes = load_boxes(args.split_input) if args.split_input else {}
    pairs = json.loads(args.pairs.read_text(encoding="utf-8")) if args.pairs else []
    result = {"per_split": per_split_stats(assignments, rows, boxes), "leakage": leakage(assignments, rows, pairs)}
    text = json.dumps(result, indent=1)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    lk = result["leakage"]
    return 1 if lk["groups_in_multiple_splits"] or lk["records_in_multiple_splits"] or lk["cross_split_pairs"] else 0


if __name__ == "__main__":
    sys.exit(main())
