"""Verify a downloaded YOLO-style detection dataset BEFORE conversion (no data is stored in the repo).

Checks:
- layout: images dir and labels dir exist;
- counts: images, label files, (optional) ``--expected-images``;
- pairing: images without label, labels without image, duplicate stems (ambiguous pairing);
- non-image files in the images tree (by extension AND magic bytes; e.g. .txt, .DS_Store,
  a .jpg that is actually HTML from a failed download);
- labels: 5 fields per line, numeric, class id in ``--num-classes`` range, normalised coords in
  [0, 1] (with tolerance), non-degenerate boxes; per-class histogram; empty label files (negatives).

Exit code 0 = no errors, 1 = errors found, 2 = usage problem. ``--report`` writes JSON.
Stdlib only.

Example (Ahmed 2025 after manual download, see fetch_ahmed2025.md)::

    python verify_dataset.py --images <root>/Dataset/Images \
        --labels <root>/Dataset/Annotations/Darknet_YOLO --num-classes <n> \
        --expected-images 6313 --report ../processed/ahmed2025/verify_report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
IGNORED_NAMES = {"Thumbs.db", "desktop.ini", ".DS_Store"}
COORD_TOLERANCE = 1e-3
MAX_EXAMPLES = 20


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

        return {
            "images": self.images,
            "label_files": self.label_files,
            "empty_label_files": self.empty_label_files,
            "boxes": self.boxes,
            "class_histogram": dict(sorted(self.class_histogram.items(), key=lambda kv: int(kv[0]))),
            "images_without_label": cap(self.images_without_label),
            "labels_without_image": cap(self.labels_without_image),
            "duplicate_stems": cap(self.duplicate_stems),
            "non_image_files": cap(self.non_image_files),
            "extension_mismatch": cap(self.extension_mismatch),
            "label_errors": cap(self.label_errors),
            "errors": self.errors,
            "warnings": self.warnings,
            "ok": not self.errors,
        }


def check_label_lines(text: str, num_classes: int, origin: str) -> tuple[list[int], list[str]]:
    """Return (class ids of valid boxes, problems)."""
    ids: list[int] = []
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
        ids.append(cid)
    return ids, problems


def verify(
    images_dir: Path, labels_dir: Path, num_classes: int, *, expected_images: int | None = None, root: Path | None = None
) -> Report:
    rep = Report()
    root = root or images_dir.parent
    for d, name in ((images_dir, "images"), (labels_dir, "labels")):
        if not d.is_dir():
            rep.errors.append(f"{name} dir not found: {d}")
    if rep.errors:
        return rep

    def rel(p: Path) -> str:
        try:
            return p.relative_to(root).as_posix()
        except ValueError:
            return p.as_posix()

    image_stems: Counter[str] = Counter()
    image_paths: dict[str, Path] = {}
    for p in sorted(images_dir.rglob("*")):
        if not p.is_file() or p.name in IGNORED_NAMES:
            continue
        with p.open("rb") as fh:
            kind = sniff_image(fh.read(16))
        if p.suffix.lower() not in IMAGE_EXTENSIONS or kind is None:
            rep.non_image_files.append(rel(p))
            continue
        ext_kind = {".jpg": "jpeg", ".jpeg": "jpeg"}.get(p.suffix.lower(), p.suffix.lower().lstrip("."))
        if ext_kind != kind:
            rep.extension_mismatch.append(f"{rel(p)} is {kind}")
        rep.images += 1
        image_stems[p.stem] += 1
        image_paths[p.stem] = p
    rep.duplicate_stems = sorted(s for s, n in image_stems.items() if n > 1)

    label_stems: set[str] = set()
    hist: Counter[int] = Counter()
    for p in sorted(labels_dir.rglob("*.txt")):
        if p.name.lower() in {"classes.txt", "obj.names"}:
            continue
        rep.label_files += 1
        label_stems.add(p.stem)
        if p.stem not in image_stems:
            rep.labels_without_image.append(rel(p))
        text = p.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            rep.empty_label_files += 1
        ids, problems = check_label_lines(text, num_classes, rel(p))
        hist.update(ids)
        rep.label_errors.extend(problems)
    rep.boxes = sum(hist.values())
    rep.class_histogram = {str(k): v for k, v in hist.items()}
    rep.images_without_label = sorted(rel(image_paths[s]) for s in image_stems if s not in label_stems)

    if rep.duplicate_stems:
        rep.errors.append(f"{len(rep.duplicate_stems)} duplicate image stems (pairing by stem is ambiguous)")
    if rep.label_errors:
        rep.errors.append(f"{len(rep.label_errors)} invalid label lines")
    if rep.labels_without_image:
        rep.errors.append(f"{len(rep.labels_without_image)} label files without image")
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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--images", type=Path, required=True)
    p.add_argument("--labels", type=Path, required=True)
    p.add_argument("--num-classes", type=int, required=True)
    p.add_argument("--expected-images", type=int)
    p.add_argument("--report", type=Path)
    args = p.parse_args(argv)
    if args.num_classes <= 0:
        print("--num-classes must be positive", file=sys.stderr)
        return 2
    rep = verify(args.images, args.labels, args.num_classes, expected_images=args.expected_images)
    out = rep.to_dict()
    text = json.dumps(out, indent=1)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
