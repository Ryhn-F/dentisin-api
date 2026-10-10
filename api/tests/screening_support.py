"""Helpers shared by the screening tests (fakes, bodies, synthetic photos).

Everything here is TEST-ONLY: the params profiles come from
``backend/triage/tests/fixtures/params_profiles.json`` (``clinical: false``) and the
photos are synthetic (no patient data exists anywhere in this repo).
"""

from __future__ import annotations

import io
import json
from functools import cache
from typing import Any, Final

import numpy as np
from app.services.detector import Detection
from app.services.quality import QualityOutcome
from app.services.screening_rules import ScreeningRules, load_screening_rules
from app.settings import Settings
from PIL import Image
from triage.paths import CASES_PATH, PROFILES_PATH

from .conftest import make_settings

SYMPTOM_IDS: Final = (
    "airway_or_swallowing",
    "lethargy",
    "trismus",
    "facial_swelling",
    "fever_with_toothache",
    "pus_or_fistula",
    "dental_trauma",
    "night_pain",
    "white_spot",
)
RED_FLAG_IDS: Final = SYMPTOM_IDS[:7]
PHOTO_MARKER: Final = b"PHOTO-BYTES-MUST-NEVER-LEAK"


def symptoms(**answers: str) -> dict[str, str]:
    base = dict.fromkeys(SYMPTOM_IDS, "no")
    base.update(answers)
    return base


def evaluate_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "detections": [],
        "photo_status": "assessed",
        "symptoms": symptoms(),
        "age_months": 96,
        "pain": 0,
    }
    body.update(overrides)
    return body


def analyze_body(photo_ref: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "photo_ref": photo_ref,
        "symptoms": symptoms(),
        "age_months": 96,
        "pain": 0,
    }
    body.update(overrides)
    return body


def screening_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {"ENABLE_SCREENING_API": True}
    base.update(overrides)
    return make_settings(**base)


@cache
def profiles() -> dict[str, dict[str, Any]]:
    data: dict[str, dict[str, Any]] = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))[
        "profiles"
    ]
    return data


@cache
def rules_for_profile(name: str) -> ScreeningRules:
    """Screening runtime built with TEST params (never clinical, never 'approved')."""
    return load_screening_rules(profiles()[name]["values"])


@cache
def shared_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    return cases


class FakeDetector:
    """Deterministic detector returning fixed findings (with boxes)."""

    name = "fake-test-detector"
    model_version = "0.0.0-test"

    def __init__(self, detections: list[Detection] | None = None) -> None:
        self.detections = detections if detections is not None else []
        self.calls = 0
        self.seen_bytes: list[int] = []

    async def detect(self, image: bytes) -> list[Detection]:
        self.calls += 1
        self.seen_bytes.append(len(image))
        return list(self.detections)


class ExplodingDetector(FakeDetector):
    async def detect(self, image: bytes) -> list[Detection]:
        raise RuntimeError("model crashed " + image[:8].hex())


class StubQualityGate:
    """Quality gate stub with a fixed verdict (the real reference gate is tested too)."""

    name = "stub"
    available = True

    def __init__(self, passed: bool = True, config_status: str = "PLACEHOLDER") -> None:
        self.passed = passed
        self.config_status = config_status
        self.calls = 0

    def check(self, image: bytes) -> QualityOutcome:
        self.calls += 1
        return QualityOutcome(
            self.passed, () if self.passed else ("blurry",), "stub", {"bytes": len(image)}
        )


def mouth_png(dark: bool = False, size: tuple[int, int] = (320, 240)) -> bytes:
    """Synthetic 'mouth' (ml/detection quality_gate_vectors 'mouth_sharp' generator):
    lips background, a band of bright low-saturation 'teeth' with dark gaps."""
    w, h = size
    lip, tooth, gap = (170, 60, 70), (235, 228, 210), (70, 25, 30)
    if dark:
        lip, tooth, gap = (30, 10, 12), (45, 44, 40), (10, 4, 5)
    img = np.empty((h, w, 3), dtype=np.uint8)
    img[:, :] = lip
    x0, y0, x1, y1 = int(w * 0.25), int(h * 0.35), int(w * 0.75), int(h * 0.65)
    for x in range(x0, x1):
        img[y0:y1, x] = gap if (x - x0) % 16 < 2 else tooth
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="PNG")
    return buf.getvalue() + PHOTO_MARKER  # trailing bytes are ignored by PNG decoders
