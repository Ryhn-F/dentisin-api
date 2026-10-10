"""Derive HEURISTIC patient groups for Ahmed et al. 2025 (Zenodo 14827784) from filenames + EXIF.

The dataset has NO patient-id field. Evidence (inspected 2026-10-09, see ../DATASET_REPORT.md):

* Main study (``retractors``/``no_retractors``, 5,505 images): filenames
  ``anonymous_<AAA>-<BBB>-<NNN>-<RR>_<ms epoch>_<View>_View.jpg`` (``-`` or ``_`` separators).
  ``AAA-BBB-NNN`` is an app *record* number; ``RR`` is ``00`` for no_retractors and ``01`` for
  retractors; the 5 views of one record share one millisecond timestamp. A record id never occurs
  in both subsets, yet the paper states each child was photographed with AND without retractors:
  records alternate in time (retractors NNN -> no_retractors NNN+1, median gap ~9 min; next child
  ~34 min later). So one child = (usually) two consecutive records. EXIF is stripped.
* Pilot (760 images): ``anonymous-<view>View-<ms epoch>.jpg``, one timestamp PER IMAGE; EXIF
  Make/Model present (3 Samsung models). Views are taken in sequence per child.
* Pilot <-> main OVERLAP: on 2024-10-02 the main records ``003-103-*`` and pilot images from one
  device were taken minutes apart, and 65 pilot images are near-identical copies (256-bit dHash
  distance 0, 32x32 correlation > 0.95) of main images -> the same children are in both subsets.

Rules (deterministic; generous on purpose -- merging two children is harmless, splitting one child
across train/test is a leak):

* main: sort records by time; link consecutive records if gap <= ``link_gap_s`` (any subsets), or if a
  ``retractors`` record is followed by a ``no_retractors`` record within ``pair_gap_s``.
* pilot: per EXIF device model timeline, a gap > ``pilot_gap_s`` starts a new group.
* pilot <-> main: a pilot image within ``pilot_main_gap_s`` of a main record merges both groups.

Outputs (``--out-dir``; small text only, no images):

* ``groups.csv``        -- image, patient_id (heuristic group), subset, view, record_id, ts_ms, device,
  n_labels, label_defect (YOLO lines outside the image), for ``make_splits.py --group-mode session_csv``;
* ``split_input.jsonl`` -- one line per image ``{"image", "boxes": [{"src": <source class>}...]}``.
  NOT canonical (class mapping still needs the dentist); make_splits only uses ``boxes`` emptiness;
* ``groups_summary.json`` -- counts, rules, group-size histogram.

Read-only on the dataset: works on an extracted root (containing ``Dataset/``) or IN PLACE on the zip.

    python ahmed2025_groups.py --zip <download>/Dataset.zip --out-dir ../processed/ahmed2025
    python ahmed2025_groups.py --root ../raw/ahmed2025 --out-dir ../processed/ahmed2025
"""

from __future__ import annotations

import argparse
import bisect
import csv
import io
import json
import re
import sys
import zipfile
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from verify_dataset import check_label_lines

IMAGES_PREFIX = "Dataset/Images/"
LABELS_PREFIX = "Dataset/Annotations/Darknet_YOLO/"
DEFAULT_CLASS_NAMES = ("D", "d")
LINK_GAP_S = 600.0
PAIR_GAP_S = 1800.0
PILOT_GAP_S = 900.0
PILOT_MAIN_GAP_S = 900.0

MAIN_RE = re.compile(r"^anonymous_(?P<a>\d{3})[-_](?P<b>\d{3})[-_](?P<n>\d+)[-_](?P<r>\d{2})_(?P<ts>\d{13})_(?P<view>[A-Za-z_]+?)_View$")
PILOT_RE = re.compile(r"^anonymous-(?P<view>[a-zA-Z]+)View-(?P<ts>\d{13})$")


class GroupError(ValueError):
    pass


@dataclass(frozen=True)
class ImageInfo:
    image: str  # path relative to the dataset root, e.g. Dataset/Images/pilot/Frontal/x.jpg
    kind: str  # "main" | "pilot"
    subset: str  # retractors | no_retractors | pilot
    view: str
    ts_ms: int
    record_id: str  # main: AAA-BBB-NNN ; pilot: ""


def parse_image_name(rel: str) -> ImageInfo:
    p = PurePosixPath(rel)
    if len(p.parts) < 4:
        raise GroupError(f"unexpected image path {rel}")
    subset = p.parts[2]
    m = MAIN_RE.match(p.stem)
    if m:
        return ImageInfo(rel, "main", subset, m["view"], int(m["ts"]), f"{m['a']}-{m['b']}-{m['n']}")
    m = PILOT_RE.match(p.stem)
    if m:
        return ImageInfo(rel, "pilot", subset, m["view"], int(m["ts"]), "")
    raise GroupError(f"filename does not match the known Ahmed 2025 patterns: {rel}")


def group_main(infos: Sequence[ImageInfo], *, link_gap_s: float = LINK_GAP_S, pair_gap_s: float = PAIR_GAP_S) -> dict[str, str]:
    """Record-level chaining -> {image: group id}."""
    records: dict[str, tuple[int, str]] = {}
    for i in infos:
        ts, subset = records.setdefault(i.record_id, (i.ts_ms, i.subset))
        if (ts, subset) != (i.ts_ms, i.subset):
            raise GroupError(f"record {i.record_id} has several timestamps/subsets")
    order = sorted(records, key=lambda r: (records[r][0], r))
    group_of: dict[str, str] = {}
    current = ""
    for prev, rec in zip([None, *order], order, strict=False):
        if prev is None:
            current = rec
        else:
            gap = (records[rec][0] - records[prev][0]) / 1000
            pair = records[prev][1] == "retractors" and records[rec][1] == "no_retractors" and gap <= pair_gap_s
            if not (gap <= link_gap_s or pair):
                current = rec
        group_of[rec] = f"main:{current}"
    return {i.image: group_of[i.record_id] for i in infos}


def group_pilot(infos: Sequence[ImageInfo], devices: Mapping[str, str], *, gap_s: float = PILOT_GAP_S) -> dict[str, str]:
    """Per-device time sessions -> {image: group id}."""
    by_dev: dict[str, list[ImageInfo]] = {}
    for i in infos:
        by_dev.setdefault(devices.get(i.image) or "unknown", []).append(i)
    out: dict[str, str] = {}
    for dev, items in sorted(by_dev.items()):
        items.sort(key=lambda i: (i.ts_ms, i.image))
        start = items[0].ts_ms
        prev = start
        for i in items:
            if (i.ts_ms - prev) / 1000 > gap_s:
                start = i.ts_ms
            out[i.image] = f"pilot:{dev}:{start}"
            prev = i.ts_ms
    return out


def link_pilot_to_main(
    pilot: Sequence[ImageInfo], main: Sequence[ImageInfo], groups: Mapping[str, str], *, gap_s: float = PILOT_MAIN_GAP_S
) -> dict[str, str]:
    """Merge each pilot group with the group of the nearest-in-time main record (if within ``gap_s``)."""
    parent: dict[str, str] = {g: g for g in set(groups.values())}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    records = sorted({(i.ts_ms, i.image) for i in main})
    times = [t for t, _ in records]
    for i in pilot:
        k = bisect.bisect_left(times, i.ts_ms)
        cands = [j for j in (k - 1, k) if 0 <= j < len(times)]
        if not cands:
            continue
        j = min(cands, key=lambda j: (abs(times[j] - i.ts_ms), j))
        if abs(times[j] - i.ts_ms) / 1000 <= gap_s:
            a, b = find(groups[i.image]), find(groups[records[j][1]])
            if a != b:
                lo, hi = sorted((a, b))
                parent[hi] = lo
    return {img: find(g) for img, g in groups.items()}


def exif_model(data: bytes) -> str | None:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        model = im.getexif().get(272)
    return str(model).strip() if model else None


@dataclass
class Source:
    """Read-only view of the dataset: image paths, a byte loader, and YOLO label texts."""

    images: list[str]
    read: Callable[[str], bytes]
    labels: dict[str, str]  # label stem -> text

    @staticmethod
    def from_zip(zf: zipfile.ZipFile) -> Source:
        names = [i.filename for i in zf.infolist() if not i.is_dir()]
        images = sorted(n for n in names if n.startswith(IMAGES_PREFIX) and n.lower().endswith((".jpg", ".jpeg", ".png")))
        labels = {PurePosixPath(n).stem: zf.read(n).decode("utf-8", errors="replace")
                  for n in names if n.startswith(LABELS_PREFIX) and n.endswith(".txt")}
        return Source(images, zf.read, labels)

    @staticmethod
    def from_root(root: Path) -> Source:
        img_dir, lab_dir = root / IMAGES_PREFIX, root / LABELS_PREFIX
        if not img_dir.is_dir():
            raise GroupError(f"not found: {img_dir}")
        images = sorted(p.relative_to(root).as_posix() for p in img_dir.rglob("*")
                        if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png"))
        labels = {p.stem: p.read_text(encoding="utf-8", errors="replace") for p in lab_dir.rglob("*.txt")} if lab_dir.is_dir() else {}
        return Source(images, lambda rel: (root / rel).read_bytes(), labels)


def build(
    src: Source,
    *,
    class_names: Sequence[str] = DEFAULT_CLASS_NAMES,
    link_gap_s: float = LINK_GAP_S,
    pair_gap_s: float = PAIR_GAP_S,
    pilot_gap_s: float = PILOT_GAP_S,
    pilot_main_gap_s: float = PILOT_MAIN_GAP_S,
    model_fn: Callable[[bytes], str | None] = exif_model,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    infos = [parse_image_name(rel) for rel in src.images]
    if not infos:
        raise GroupError("no images")
    main = [i for i in infos if i.kind == "main"]
    pilot = [i for i in infos if i.kind == "pilot"]
    devices = {i.image: model_fn(src.read(i.image)) or "unknown" for i in pilot}
    groups = {**group_main(main, link_gap_s=link_gap_s, pair_gap_s=pair_gap_s), **group_pilot(pilot, devices, gap_s=pilot_gap_s)}
    before = {i.image for i in pilot if groups[i.image].startswith("pilot:")}
    groups = link_pilot_to_main(pilot, main, groups, gap_s=pilot_main_gap_s)
    linked = sum(1 for img in before if not groups[img].startswith("pilot:"))
    rows: list[dict[str, Any]] = []
    for i in infos:
        text = src.labels.get(PurePosixPath(i.image).stem, "")
        boxes, problems = check_label_lines(text, len(class_names), i.image)
        rows.append({
            "image": i.image, "patient_id": groups[i.image], "subset": i.subset, "view": i.view,
            "record_id": i.record_id, "ts_ms": i.ts_ms, "device": devices.get(i.image, ""),
            "src_boxes": [class_names[c] for c, _, _ in boxes], "label_defect": len(problems),
        })
    rows.sort(key=lambda r: r["image"])
    sizes = Counter(Counter(r["patient_id"] for r in rows).values())
    image_stems = {PurePosixPath(i.image).stem for i in infos}
    summary = {
        "images": len(rows),
        "images_main": len(main),
        "images_pilot": len(pilot),
        "main_records": len({i.record_id for i in main}),
        "groups": len({r["patient_id"] for r in rows}),
        "groups_main": len({r["patient_id"] for r in rows if r["patient_id"].startswith("main:")}),
        "groups_pilot": len({r["patient_id"] for r in rows if r["patient_id"].startswith("pilot:")}),
        "pilot_images_merged_into_main_groups": linked,
        "group_size_hist": {str(k): v for k, v in sorted(sizes.items())},
        "pilot_devices": dict(Counter(devices.values())),
        "images_with_boxes": sum(1 for r in rows if r["src_boxes"]),
        "boxes_by_source_class": dict(Counter(b for r in rows for b in r["src_boxes"])),
        "images_with_label_defects": sum(1 for r in rows if r["label_defect"]),
        "orphan_label_files": sorted(s for s in src.labels if s not in image_stems),
        "rules": {"link_gap_s": link_gap_s, "pair_gap_s": pair_gap_s, "pilot_gap_s": pilot_gap_s, "pilot_main_gap_s": pilot_main_gap_s,
                  "main": "consecutive records linked if gap <= link_gap_s, or retractors->no_retractors within pair_gap_s",
                  "pilot": "per EXIF Model timeline; gap > pilot_gap_s starts a new group",
                  "pilot_main": "pilot image within pilot_main_gap_s of a main record -> groups merged"},
    }
    return rows, summary


def write_outputs(rows: Iterable[Mapping[str, Any]], summary: Mapping[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = list(rows)
    cols = ["image", "patient_id", "subset", "view", "record_id", "ts_ms", "device", "n_labels", "label_defect"]
    with (out_dir / "groups.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({**{k: r[k] for k in cols if k in r}, "n_labels": len(r["src_boxes"])})
    with (out_dir / "split_input.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps({"image": r["image"], "boxes": [{"src": b} for b in r["src_boxes"]]}) + "\n")
    (out_dir / "groups_summary.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src_g = p.add_mutually_exclusive_group(required=True)
    src_g.add_argument("--zip", type=Path, help="Dataset.zip, read in place")
    src_g.add_argument("--root", type=Path, help="extracted root that contains Dataset/")
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--class-names", default=",".join(DEFAULT_CLASS_NAMES))
    p.add_argument("--link-gap-s", type=float, default=LINK_GAP_S)
    p.add_argument("--pair-gap-s", type=float, default=PAIR_GAP_S)
    p.add_argument("--pilot-gap-s", type=float, default=PILOT_GAP_S)
    p.add_argument("--pilot-main-gap-s", type=float, default=PILOT_MAIN_GAP_S)
    args = p.parse_args(argv)
    kw: dict[str, Any] = {"class_names": args.class_names.split(","), "link_gap_s": args.link_gap_s,
                          "pair_gap_s": args.pair_gap_s, "pilot_gap_s": args.pilot_gap_s, "pilot_main_gap_s": args.pilot_main_gap_s}
    try:
        if args.zip is not None:
            with zipfile.ZipFile(args.zip) as zf:
                rows, summary = build(Source.from_zip(zf), **kw)
        else:
            rows, summary = build(Source.from_root(args.root), **kw)
    except GroupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    write_outputs(rows, summary, args.out_dir)
    print(json.dumps({k: summary[k] for k in ("images", "groups", "groups_main", "groups_pilot", "pilot_images_merged_into_main_groups",
                                              "group_size_hist")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
