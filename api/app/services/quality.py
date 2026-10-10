"""Photo quality-gate stage for ``/v1/screening/analyze`` (FR-21).

Reuses the REFERENCE implementation ``backend/ml/detection/quality_gate.py`` (+ its
``quality_gate.yaml``; all thresholds PLACEHOLDER). ``ml/detection`` is not an
installable package (``tool.uv.package = false``), so the module is loaded by file
path. It needs numpy + Pillow (``uv sync --extra vision``); when either the
libraries or the file are missing, :func:`load_reference_quality_gate` returns an
:class:`UnavailableQualityGate` and the analyze route fails closed (503) instead of
pretending the photo was checked.

The photo is decoded in memory only (JPEG/PNG, pixel cap against decompression
bombs) and never written, logged or returned.
"""

from __future__ import annotations

import importlib.util
import io
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Final, Protocol

BACKEND_ROOT: Final = Path(__file__).resolve().parents[3]
DEFAULT_QUALITY_GATE_DIR: Final = BACKEND_ROOT / "ml" / "detection"
MAX_DECODE_PIXELS: Final = 24_000_000  # ~6000 x 4000; larger -> not processable
ALLOWED_FORMATS: Final = ("JPEG", "PNG")
_MODULE_NAME: Final = "dentisai_ml_quality_gate"


@dataclass(frozen=True)
class QualityOutcome:
    passed: bool
    reasons: tuple[str, ...]
    method: str
    metrics: dict[str, float | int | str] = field(default_factory=dict)


class QualityGate(Protocol):
    name: str
    available: bool
    config_status: str

    def check(self, image: bytes) -> QualityOutcome: ...


class UnavailableQualityGate:
    """Minimal install / missing reference module: the stage reports ``unavailable``."""

    name = "unavailable"
    available = False
    config_status = "UNAVAILABLE"

    def check(self, image: bytes) -> QualityOutcome:  # pragma: no cover - never called
        raise RuntimeError("quality gate unavailable")


class ReferenceQualityGate:
    """Adapter around ``ml/detection/quality_gate.py`` (pure numpy functions)."""

    name = "ml/detection quality_gate"
    available = True

    def __init__(self, module: ModuleType, config: Any, image_lib: ModuleType, np: ModuleType):
        self._module = module
        self._config = config
        self._image = image_lib
        self._np = np
        self.config_status = str(config.status)

    def _decode(self, image: bytes) -> Any:
        pil = self._image
        try:
            with pil.open(io.BytesIO(image), formats=ALLOWED_FORMATS) as im:
                width, height = im.size
                if width * height > MAX_DECODE_PIXELS:
                    return None
                return self._np.asarray(im.convert("RGB"), dtype=self._np.uint8)
        except (OSError, ValueError, pil.DecompressionBombError, SyntaxError):
            return None

    def check(self, image: bytes) -> QualityOutcome:
        rgb = self._decode(image)
        if rgb is None:
            return QualityOutcome(False, ("not_decodable",), "decode")
        result = self._module.evaluate_quality(rgb, self._config)
        del rgb
        metrics = {
            key: value
            for key, value in dict(result.metrics).items()
            if isinstance(value, int | float | str) and not isinstance(value, bool)
        }
        return QualityOutcome(
            bool(result.passed),
            tuple(str(r) for r in result.reasons),
            str(metrics.get("teeth_method", "heuristic")),
            metrics,
        )


def load_reference_quality_gate(directory: Path = DEFAULT_QUALITY_GATE_DIR) -> QualityGate:
    """Return the reference gate, or :class:`UnavailableQualityGate` (never raises)."""
    source = directory / "quality_gate.py"
    try:
        import numpy as np  # noqa: PLC0415 - optional `vision` extra
        from PIL import Image  # noqa: PLC0415 - optional `vision` extra
    except ImportError:
        return UnavailableQualityGate()
    if not source.is_file():
        return UnavailableQualityGate()
    try:
        module = sys.modules.get(_MODULE_NAME)
        if module is None:
            spec = importlib.util.spec_from_file_location(_MODULE_NAME, source)
            if spec is None or spec.loader is None:  # pragma: no cover - defensive
                return UnavailableQualityGate()
            module = importlib.util.module_from_spec(spec)
            sys.modules[_MODULE_NAME] = module  # dataclasses resolve their module here
            spec.loader.exec_module(module)
        config = module.load_config(directory / "quality_gate.yaml")
    except (ImportError, OSError, KeyError, ValueError, TypeError, AttributeError):
        sys.modules.pop(_MODULE_NAME, None)
        return UnavailableQualityGate()
    return ReferenceQualityGate(module, config, Image, np)
