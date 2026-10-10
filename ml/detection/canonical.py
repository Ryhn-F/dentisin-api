"""Canonical internal annotation format for the DentisAI caries detector.

One JSON object per line (JSONL), one line per image::

    {"image": "Dataset/Images/retractors/Frontal/IMG_0001.jpg",
     "width": 1080, "height": 1440,
     "source": "ahmed2025",
     "patient_id": null,
     "boxes": [{"cls": "caries_permanent", "x1": 10.0, "y1": 20.0, "x2": 50.0, "y2": 70.0}]}

Conventions (shared by every reader/writer, eval and decode):
- Box coordinates are absolute pixels in the ORIGINAL image, continuous, top-left origin,
  ``0 <= x1 < x2 <= width`` and ``0 <= y1 < y2 <= height``.
- ``cls`` is a canonical class NAME (never a numeric id) from :data:`CLASS_NAMES`.
- An image with ``boxes == []`` is a negative (no annotated lesion), not "unlabelled".
- ``patient_id`` is a pseudonymous grouping key (null when the source has none).

This module is pure Python (no numpy) and has no side effects other than explicit file I/O
in :func:`read_jsonl` / :func:`write_jsonl`.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FORMAT_ID = "dentis-canonical-v1"

#: Model classes, in model output order (ADR 014): caries indication on a PERMANENT tooth (Ahmed label "D", YOLO id 0)
#: and on a PRIMARY tooth ("d", id 1). A box surrounds the TOOTH with visible decay, not a lesion; box size must never be
#: used for severity. No cavitated/non-cavitated distinction exists in the data (removed in v1).
CLASS_NAMES: tuple[str, ...] = ("caries_permanent", "caries_primary")
CLASS_TO_ID: dict[str, int] = {name: i for i, name in enumerate(CLASS_NAMES)}

#: Minimum box side (pixels) below which a box is considered degenerate.
MIN_BOX_SIDE_PX = 1.0


class AnnotationError(ValueError):
    """Raised when an annotation violates the canonical contract."""


@dataclass(frozen=True)
class Box:
    cls: str
    x1: float
    y1: float
    x2: float
    y2: float
    score: float | None = None  # only set for predictions

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"cls": self.cls, "x1": self.x1, "y1": self.y1, "x2": self.x2, "y2": self.y2}
        if self.score is not None:
            d["score"] = self.score
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Box:
        try:
            score = d.get("score")
            return Box(
                cls=str(d["cls"]),
                x1=float(d["x1"]),
                y1=float(d["y1"]),
                x2=float(d["x2"]),
                y2=float(d["y2"]),
                score=None if score is None else float(score),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AnnotationError(f"malformed box {d!r}: {exc}") from exc


@dataclass
class ImageAnnotation:
    image: str
    width: int
    height: int
    boxes: list[Box] = field(default_factory=list)
    source: str | None = None
    patient_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "image": self.image,
            "width": self.width,
            "height": self.height,
            "source": self.source,
            "patient_id": self.patient_id,
            "boxes": [b.to_dict() for b in self.boxes],
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> ImageAnnotation:
        try:
            return ImageAnnotation(
                image=str(d["image"]),
                width=int(d["width"]),
                height=int(d["height"]),
                boxes=[Box.from_dict(b) for b in d.get("boxes", [])],
                source=d.get("source"),
                patient_id=None if d.get("patient_id") is None else str(d["patient_id"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AnnotationError(f"malformed image record: {exc}") from exc


def validate_box(box: Box, width: int, height: int, *, allowed: Iterable[str] = CLASS_NAMES) -> list[str]:
    """Return a list of human-readable problems (empty list == valid)."""
    problems: list[str] = []
    allowed_set = set(allowed)
    if box.cls not in allowed_set:
        problems.append(f"unknown class {box.cls!r}")
    coords = (box.x1, box.y1, box.x2, box.y2)
    if any(c != c for c in coords):  # NaN check without math import
        problems.append("NaN coordinate")
        return problems
    if box.x1 < 0 or box.y1 < 0 or box.x2 > width or box.y2 > height:
        problems.append(f"box {coords} out of bounds for image {width}x{height}")
    if box.width < MIN_BOX_SIDE_PX or box.height < MIN_BOX_SIDE_PX:
        problems.append(f"degenerate box {coords} (side < {MIN_BOX_SIDE_PX}px)")
    if box.score is not None and not (0.0 <= box.score <= 1.0):
        problems.append(f"score {box.score} outside [0, 1]")
    return problems


def validate_image(ann: ImageAnnotation, *, allowed: Iterable[str] = CLASS_NAMES) -> list[str]:
    problems: list[str] = []
    if ann.width <= 0 or ann.height <= 0:
        problems.append(f"{ann.image}: non-positive image size {ann.width}x{ann.height}")
        return problems
    allowed_t = tuple(allowed)
    for i, box in enumerate(ann.boxes):
        problems.extend(f"{ann.image}: box[{i}]: {p}" for p in validate_box(box, ann.width, ann.height, allowed=allowed_t))
    return problems


def clip_box(box: Box, width: int, height: int) -> Box:
    """Clip a box to image bounds (used only when a reader is explicitly asked to clip)."""
    return Box(
        cls=box.cls,
        x1=min(max(box.x1, 0.0), float(width)),
        y1=min(max(box.y1, 0.0), float(height)),
        x2=min(max(box.x2, 0.0), float(width)),
        y2=min(max(box.y2, 0.0), float(height)),
        score=box.score,
    )


def read_jsonl(path: Path) -> list[ImageAnnotation]:
    records: list[ImageAnnotation] = []
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(ImageAnnotation.from_dict(json.loads(line)))
            except (json.JSONDecodeError, AnnotationError) as exc:
                raise AnnotationError(f"{path}:{lineno}: {exc}") from exc
    return records


def iter_jsonl_lines(records: Iterable[ImageAnnotation]) -> Iterator[str]:
    for rec in records:
        yield json.dumps(rec.to_dict(), sort_keys=True, ensure_ascii=False)


def write_jsonl(records: Iterable[ImageAnnotation], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for line in iter_jsonl_lines(records):
            fh.write(line + "\n")
