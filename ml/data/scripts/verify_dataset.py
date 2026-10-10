"""Verify a downloaded YOLO-style detection dataset BEFORE conversion (no data is stored in the repo).

Checks:
- layout: images dir and labels dir exist (or the prefixes exist inside a zip archive);
- counts: images, label files, (optional) ``--expected-images``;
- pairing: images without label, labels without image ("orphans"), duplicate stems (ambiguous pairing);
- non-image files in the images tree (by extension AND magic bytes; e.g. .txt, .DS_Store,
  a .jpg that is actually HTML from a failed download);
- labels: 5 fields per line, numeric, class id in ``--num-classes`` range, normalised coords in
  [0, 1] (with tolerance), non-degenerate boxes; per-class histogram (by id and, with
  ``--class-names``, by name); empty label files (explicit negatives); box size distribution.

Two front-ends share one core: a directory tree (``--images``/``--labels``) or a zip archive read
IN PLACE (``--zip`` + ``--images-prefix``/``--labels-prefix``), so a dataset can be verified without
extracting it (low-disk machines). Nothing in the archive is executed or written to disk.

Ahmed 2025 reality (verified 2026-10-09 on Zenodo 14827784 ``Dataset.zip``, see ../DATASET_REPORT.md):
6,265 JPEGs (record text says 6,313), only 2,245 YOLO files (images without a file = no caries
annotated), 18 orphan label files whose image is not in the archive (``--orphan-labels warn``),
2 classes with no ``classes.txt``: id 0 = ``D`` (permanent-tooth decay), id 1 = ``d`` (primary).

Exit code 0 = no errors, 1 = errors found, 2 = usage problem. ``--report`` writes JSON.
Stdlib only.

Examples::

    python verify_dataset.py --images <root>/Dataset/Images \
        --labels <root>/Dataset/Annotations/Darknet_YOLO --num-classes 2 --class-names D,d \
        --expected-images 6265 --orphan-labels warn --report ../processed/ahmed2025/verify_report.json
    python verify_dataset.py --zip <download>/Dataset.zip --images-prefix Dataset/Images \
        --labels-prefix Dataset/Annotations/Darknet_YOLO --num-classes 2 --class-names D,d \
        --expected-images 6265 --orphan-labels warn
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
IGNORED_NAMES = {"Thumbs.db", "desktop.ini", ".DS_Store"}
LABEL_META_NAMES = {"classes.txt", "obj.names"}
COORD_TOLERANCE = 1e-3
MAX_EXAMPLES = 20
#: Upper bounds of the relative box-area buckets (w*h of the normalised box).
AREA_BUCKETS = (0.0025, 0.01, 0.04, 0.16, 1.0)

OrphanPolicy = Literal["error", "warn"]
#: (relative path, first 16 bytes) for every file in the images tree
ImageEntry = tuple[str, Callable[[], bytes]]
#: (relative path, decoded text) for every ``*.txt`` in the labels tree
LabelEntry = tuple[str, Callable[[], str]]


def sniff_image(head: bytes) -> str | None:
    """Return the image type from magic bytes, or None if not a supported image."""
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"BM"):
        return "bmp"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


@dataclass
class Report:
    images: int = 0
    label_files: int = 0
    empty_label_files: int = 0
    boxes: int = 0
    class_histogram: dict[str, int] = field(default_factory=dict)
    class_names: list[str] = field(default_factory=list)
    box_area_histogram: dict[str, int] = field(default_factory=dict)
    images_per_folder: dict[str, int] = field(default_factory=dict)
    images_without_label: list[str] = field(default_factory=list)
    labels_without_image: list[str] = field(default_factory=list)
    duplicate_stems: list[str] = field(default_factory=list)
    non_image_files: list[str] = field(default_factory=list)
    extension_mismatch: list[str] = field(default_factory=list)
    label_errors: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        def cap(xs: list[str]) -> dict[str, Any]:
            return {"count": len(xs), "examples": xs[:MAX_EXAMPLES]}

        hist = dict(sorted(self.class_histogram.items(), key=lambda kv: int(kv[0])))
        out: dict[str, Any] = {
            "images": self.images,
            "label_files": self.label_files,
            "empty_label_files": self.empty_label_files,
            "boxes": self.boxes,
            "class_histogram": hist,
        }
        if self.class_names:
            out["class_histogram_by_name"] = {self.class_names[int(k)]: v for k, v in hist.items()}
        out.update({
            "box_area_histogram": self.box_area_histogram,
            "images_per_folder": dict(sorted(self.images_per_folder.items())),
            "images_without_label": cap(self.images_without_label),
            "labels_without_image": cap(self.labels_without_image),
            "duplicate_stems": cap(self.duplicate_stems),
            "non_image_files": cap(self.non_image_files),
            "extension_mismatch": cap(self.extension_mismatch),
            "label_errors": cap(self.label_errors),
            "errors": self.errors,
            "warnings": self.warnings,
            "ok": not self.errors,
        })
        return out


def area_bucket(w: float, h: float) -> str:
    a = w * h
    for hi in AREA_BUCKETS:
        if a <= hi + 1e-12:  # tolerate float rounding (0.2 * 0.2 > 0.04)
            return f"<={hi}"
    return f">{AREA_BUCKETS[-1]}"


def check_label_lines(text: str, num_classes: int, origin: str) -> tuple[list[tuple[int, float, float]], list[str]]:
    """Return ((class id, w, h) of valid boxes, problems)."""
    boxes: list[tuple[int, float, float]] = []
    problems: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        parts = line.split()
        if not parts:
            continue
        where = f"{origin}:{lineno}"
        if len(parts) != 5:
            problems.append(f"{where}: expected 5 fields, got {len(parts)}")
            continue
        try:
            cid = int(parts[0])
            cx, cy, w, h = (float(v) for v in parts[1:])
        except ValueError:
            problems.append(f"{where}: non-numeric field")
            continue
        if not 0 <= cid < num_classes:
            problems.append(f"{where}: class id {cid} outside [0, {num_classes})")
            continue
        lo, hi = -COORD_TOLERANCE, 1 + COORD_TOLERANCE
        if not all(lo <= v <= hi for v in (cx, cy, w, h)):
            problems.append(f"{where}: coordinate outside [0, 1]")
            continue
        if w <= 0 or h <= 0:
            problems.append(f"{where}: degenerate box (w={w}, h={h})")
            continue
        if cx - w / 2 < lo or cy - h / 2 < lo or cx + w / 2 > hi or cy + h / 2 > hi:
            problems.append(f"{where}: box extends beyond image")
            continue
        boxes.append((cid, w, h))
    return boxes, problems


def verify_entries(
    images: Iterable[ImageEntry],
    labels: Iterable[LabelEntry],
    num_classes: int,
    *,
    expected_images: int | None = None,
    orphan_labels: OrphanPolicy = "error",
    class_names: Sequence[str] = (),
) -> Report:
    """Core check over abstract (path, loader) entries; paths are POSIX-style relative paths."""
    rep = Report(class_names=list(class_names))
    if class_names and len(class_names) != num_classes:
        rep.errors.append(f"--class-names has {len(class_names)} names but --num-classes is {num_classes}")
        return rep
    image_stems: Counter[str] = Counter()
    image_paths: dict[str, str] = {}
    folders: Counter[str] = Counter()
    for rel, head_fn in sorted(images, key=lambda e: e[0]):
        p = PurePosixPath(rel)
        if p.name in IGNORED_NAMES:
            continue
        kind = sniff_image(head_fn())
        if p.suffix.lower() not in IMAGE_EXTENSIONS or kind is None:
            rep.non_image_files.append(rel)
            continue
        ext_kind = {".jpg": "jpeg", ".jpeg": "jpeg"}.get(p.suffix.lower(), p.suffix.lower().lstrip("."))
        if ext_kind != kind:
            rep.extension_mismatch.append(f"{rel} is {kind}")
        rep.images += 1
        image_stems[p.stem] += 1
        image_paths[p.stem] = rel
        folders[str(p.parent)] += 1
    rep.images_per_folder = dict(folders)
    rep.duplicate_stems = sorted(s for s, n in image_stems.items() if n > 1)

    label_stems: set[str] = set()
    hist: Counter[int] = Counter()
    areas: Counter[str] = Counter()
    for rel, text_fn in sorted(labels, key=lambda e: e[0]):
        p = PurePosixPath(rel)
        if p.suffix.lower() != ".txt" or p.name.lower() in LABEL_META_NAMES:
            continue
        rep.label_files += 1
        label_stems.add(p.stem)
        if p.stem not in image_stems:
            rep.labels_without_image.append(rel)
        text = text_fn()
        if not text.strip():
            rep.empty_label_files += 1
        boxes, problems = check_label_lines(text, num_classes, rel)
        hist.update(cid for cid, _, _ in boxes)
        areas.update(area_bucket(w, h) for _, w, h in boxes)
        rep.label_errors.extend(problems)
    rep.boxes = sum(hist.values())
    rep.class_histogram = {str(k): v for k, v in hist.items()}
    order = [*(f"<={hi}" for hi in AREA_BUCKETS), f">{AREA_BUCKETS[-1]}"]
    rep.box_area_histogram = {b: areas[b] for b in order if areas[b]}
    rep.images_without_label = sorted(image_paths[s] for s in image_stems if s not in label_stems)

    if rep.duplicate_stems:
        rep.errors.append(f"{len(rep.duplicate_stems)} duplicate image stems (pairing by stem is ambiguous)")
    if rep.label_errors:
        rep.errors.append(f"{len(rep.label_errors)} invalid label lines")
    if rep.labels_without_image:
        msg = f"{len(rep.labels_without_image)} label files without image"
        (rep.errors if orphan_labels == "error" else rep.warnings).append(msg)
    if rep.images_without_label:
        rep.warnings.append(f"{len(rep.images_without_label)} images without label file (negatives or missing?)")
    if rep.non_image_files:
        rep.warnings.append(f"{len(rep.non_image_files)} non-image files in images tree")
    if rep.extension_mismatch:
        rep.warnings.append(f"{len(rep.extension_mismatch)} files whose extension does not match content")
    if expected_images is not None and rep.images != expected_images:
        rep.errors.append(f"expected {expected_images} images, found {rep.images}")
    if rep.images == 0:
        rep.errors.append("no images found")
    return rep


def _read_head(path: Path) -> Callable[[], bytes]:
    def _f() -> bytes:
        with path.open("rb") as fh:
            return fh.read(16)

    return _f


def _read_text(path: Path) -> Callable[[], str]:
    return lambda: path.read_text(encoding="utf-8", errors="replace")


def verify(
    images_dir: Path,
    labels_dir: Path,
    num_classes: int,
    *,
    expected_images: int | None = None,
    root: Path | None = None,
    orphan_labels: OrphanPolicy = "error",
    class_names: Sequence[str] = (),
) -> Report:
    """Directory front-end: paths in the report are relative to ``root`` (default: images parent)."""
    missing = [f"{name} dir not found: {d}" for d, name in ((images_dir, "images"), (labels_dir, "labels")) if not d.is_dir()]
    if missing:
        return Report(errors=missing)
    base = root or images_dir.parent

    def rel(p: Path) -> str:
        try:
            return p.relative_to(base).as_posix()
        except ValueError:
            return p.as_posix()

    imgs: list[ImageEntry] = [(rel(p), _read_head(p)) for p in images_dir.rglob("*") if p.is_file()]
    labs: list[LabelEntry] = [
        (rel(p), _read_text(p)) for p in labels_dir.rglob("*.txt")
    ]
    return verify_entries(imgs, labs, num_classes, expected_images=expected_images, orphan_labels=orphan_labels,
                          class_names=class_names)


def verify_zip(
    zf: zipfile.ZipFile,
    images_prefix: str,
    labels_prefix: str,
    num_classes: int,
    *,
    expected_images: int | None = None,
    orphan_labels: OrphanPolicy = "error",
    class_names: Sequence[str] = (),
) -> Report:
    """Zip front-end: members are read in memory (never extracted); report paths = member names."""
    ip, lp = images_prefix.rstrip("/") + "/", labels_prefix.rstrip("/") + "/"
    members = [i for i in zf.infolist() if not i.is_dir()]
    missing = [f"{name} prefix not found in archive: {pre}" for pre, name in ((ip, "images"), (lp, "labels"))
               if not any(m.filename.startswith(pre) for m in members)]
    if missing:
        return Report(errors=missing)

    def head(name: str) -> Callable[[], bytes]:
        def _f() -> bytes:
            with zf.open(name) as fh:
                return fh.read(16)

        return _f

    def text(name: str) -> Callable[[], str]:
        return lambda: zf.read(name).decode("utf-8", errors="replace")

    imgs: list[ImageEntry] = [(m.filename, head(m.filename)) for m in members if m.filename.startswith(ip)]
    labs: list[LabelEntry] = [(m.filename, text(m.filename)) for m in members
                              if m.filename.startswith(lp) and m.filename.lower().endswith(".txt")]
    return verify_entries(imgs, labs, num_classes, expected_images=expected_images, orphan_labels=orphan_labels,
                          class_names=class_names)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--images", type=Path)
    p.add_argument("--labels", type=Path)
    p.add_argument("--zip", type=Path, help="verify a zip archive in place instead of directories")
    p.add_argument("--images-prefix", default="Dataset/Images")
    p.add_argument("--labels-prefix", default="Dataset/Annotations/Darknet_YOLO")
    p.add_argument("--num-classes", type=int, required=True)
    p.add_argument("--class-names", default="", help="comma-separated names in id order, e.g. D,d")
    p.add_argument("--expected-images", type=int)
    p.add_argument("--orphan-labels", choices=("error", "warn"), default="error")
    p.add_argument("--report", type=Path)
    args = p.parse_args(argv)
    if args.num_classes <= 0:
        print("--num-classes must be positive", file=sys.stderr)
        return 2
    if args.zip is None and (args.images is None or args.labels is None):
        print("give --images and --labels, or --zip", file=sys.stderr)
        return 2
    names = [n for n in args.class_names.split(",") if n] if args.class_names else []
    kw: dict[str, Any] = {"expected_images": args.expected_images, "orphan_labels": args.orphan_labels, "class_names": names}
    if args.zip is not None:
        with zipfile.ZipFile(args.zip) as zf:
            rep = verify_zip(zf, args.images_prefix, args.labels_prefix, args.num_classes, **kw)
    else:
        rep = verify(args.images, args.labels, args.num_classes, **kw)
    out = rep.to_dict()
    text = json.dumps(out, indent=1)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
