"""Pluggable detector interface for the OPTIONAL cloud QA/second-opinion hook.

Only ``NullDetector`` ships: no model weights, no ML runtime, no AGPL code
(ADR 001/008 — Ultralytics/YOLOv8 must never be added here). A real detector
must be a permissively licensed bounding-box model exported by ml/detection,
recorded in ml/data/LICENSES.md, and produce boxes in the canonical format
(absolute pixels, class names from ml/detection/canonical.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Detection:
    cls: str
    x1: float
    y1: float
    x2: float
    y2: float
    score: float


class Detector(Protocol):
    name: str
    model_version: str

    async def detect(self, image: bytes) -> list[Detection]: ...


class NullDetector:
    """Returns no detections. Lets the plumbing (auth, consent gating, audit)
    be exercised without shipping a model."""

    name = "null"
    model_version = "0.0.0-null"

    async def detect(self, image: bytes) -> list[Detection]:
        return []
