"""Pluggable detector interface for the screening photo pipeline.

``NullDetector`` is the default (no model configured). ``LiteRtDetector`` runs a
permissively licensed bounding-box model exported by ml/detection (ADR 001/013:
YOLOX-Nano, Apache-2.0 code) from an export bundle folder
``{model.tflite, labels.json}``. Pre- and post-processing are the REFERENCE
implementations in ``ml/detection`` (``preprocess.py`` / ``decode.py``, DECODE_SPEC),
never re-implemented here. Ultralytics/YOLOv8 must never be added (ADR 001/008).

Boxes are absolute pixels of the decoded photo and are for the UI overlay ONLY
(ADR 014); class names come from ml/detection/canonical.py. Photo bytes are
decoded in memory and never written, logged or returned.
"""

from __future__ import annotations

import hashlib
import importlib
import io
import json
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Final, Protocol

import anyio

BACKEND_ROOT: Final = Path(__file__).resolve().parents[3]
ML_DETECTION_DIR: Final = BACKEND_ROOT / "ml" / "detection"
MAX_DECODE_PIXELS: Final = 24_000_000
ALLOWED_FORMATS: Final = ("JPEG", "PNG")

Runner = Callable[[Any], Sequence[Any]]


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


class PhotoNotDecodableError(ValueError):
    """The bytes are not a JPEG/PNG within the pixel cap."""


def _reference_modules(directory: Path) -> tuple[ModuleType, ModuleType]:
    """``preprocess`` and ``decode`` from ml/detection (plain scripts, not a package;
    ``preprocess`` imports its siblings by bare name, so the folder goes on sys.path)."""
    path = str(directory)
    added = path not in sys.path
    if added:
        sys.path.append(path)
    try:
        return importlib.import_module("preprocess"), importlib.import_module("decode")
    finally:
        if added:
            sys.path.remove(path)


def litert_runner(model_path: Path) -> Runner:  # pragma: no cover - needs a LiteRT interpreter
    """Float/INT8 .tflite runner (ai-edge-litert, else tensorflow.lite), as
    ml/detection/check_int8_parity.tflite_runner."""
    import numpy as np  # noqa: PLC0415 - optional `vision` extra

    try:
        interpreter = importlib.import_module("ai_edge_litert.interpreter").Interpreter
    except ImportError:
        interpreter = importlib.import_module("tensorflow.lite").Interpreter
    interp = interpreter(model_path=str(model_path))
    interp.allocate_tensors()
    inp = interp.get_input_details()[0]
    outs = interp.get_output_details()

    def run(x: Any) -> list[Any]:
        data = x
        if inp["dtype"] in (np.int8, np.uint8):
            scale, zp = inp["quantization"]
            info = np.iinfo(inp["dtype"])
            data = np.clip(np.round(x / scale + zp), info.min, info.max)
        interp.set_tensor(inp["index"], data.astype(inp["dtype"]))
        interp.invoke()
        res = []
        for o in outs:
            v = interp.get_tensor(o["index"])
            if o["dtype"] in (np.int8, np.uint8) and o["quantization"][0]:
                v = o["quantization"][0] * (v.astype(np.float64) - o["quantization"][1])
            res.append(v)
        return res

    return run


class LiteRtDetector:
    """Export bundle -> detections (photo pixels), using labels.json for input spec,
    decode, NMS and the per-class operating thresholds."""

    def __init__(
        self,
        labels: Mapping[str, Any],
        runner: Runner,
        *,
        directory: Path = ML_DETECTION_DIR,
    ) -> None:
        import numpy as np  # noqa: PLC0415 - optional `vision` extra
        from PIL import Image  # noqa: PLC0415 - optional `vision` extra

        self._np = np
        self._image = Image
        self._pre, self._dec = _reference_modules(directory)
        self._spec = self._pre.InputSpec.from_labels(labels)
        self._decode_spec = labels["output"]["decode"]
        self._roles = {int(t["index"]): str(t["role"]) for t in labels["output"]["tensors"]}
        post = labels["output"]["postprocess"]
        self._nms_iou = float(post["nms_iou_threshold"])
        self._max_detections = int(post["max_detections"])
        self._class_agnostic = bool(post["class_agnostic_nms"])
        classes = sorted(labels["classes"], key=lambda c: int(c["id"]))
        self._class_names = [str(c["name"]) for c in classes]
        scores = labels["thresholds"]["score"]
        thresholds = [scores.get(name) for name in self._class_names]
        if any(t is None for t in thresholds):
            raise ValueError("labels.json has no operating threshold for every class")
        self._thresholds = [float(t) for t in thresholds]
        self._runner = runner
        self._lock = threading.Lock()  # one interpreter, not thread-safe
        self.name = str(labels["model"]["name"])
        self.model_version = str(labels["model"]["version"])

    def _decode_photo(self, image: bytes) -> Any:
        pil = self._image
        try:
            with pil.open(io.BytesIO(image), formats=ALLOWED_FORMATS) as im:
                width, height = im.size
                if width * height > MAX_DECODE_PIXELS:
                    raise PhotoNotDecodableError("too many pixels")
                return self._np.asarray(im.convert("RGB"), dtype=self._np.uint8)
        except (OSError, ValueError, pil.DecompressionBombError, SyntaxError) as exc:
            raise PhotoNotDecodableError("not a JPEG/PNG photo") from exc

    def detect_sync(self, image: bytes) -> list[Detection]:
        np = self._np
        rgb = self._decode_photo(image)
        x, geom = self._pre.preprocess(rgb, self._spec)
        del rgb
        with self._lock:
            outputs = list(self._runner(x))
        if len(outputs) != len(self._roles):
            raise ValueError("model output count does not match labels.json")
        named = {self._roles[i]: np.asarray(o) for i, o in enumerate(outputs)}
        decoded = self._dec.decode_from_spec(
            named, self._decode_spec, self._spec.width, self._spec.height
        )
        kept = self._dec.postprocess(
            decoded,
            self._thresholds,
            self._nms_iou,
            self._max_detections,
            class_agnostic=self._class_agnostic,
        )
        if geom.method == "letterbox":
            boxes = self._dec.unletterbox(kept.boxes, geom.letterbox, geom.src_w, geom.src_h)
        else:
            boxes = self._dec.unstretch(
                kept.boxes, self._spec.width, self._spec.height, geom.src_w, geom.src_h
            )
        out: list[Detection] = []
        for box, cid, score in zip(boxes, kept.classes, kept.scores, strict=True):
            if box[2] - box[0] <= 0 or box[3] - box[1] <= 0:  # fully clipped after mapping back
                continue
            out.append(
                Detection(
                    self._class_names[int(cid)],
                    float(box[0]),
                    float(box[1]),
                    float(box[2]),
                    float(box[3]),
                    float(score),
                )
            )
        return out

    async def detect(self, image: bytes) -> list[Detection]:
        return await anyio.to_thread.run_sync(self.detect_sync, image)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_detector(
    model_dir: Path | None,
    *,
    runner_factory: Callable[[Path], Runner] = litert_runner,
    directory: Path = ML_DETECTION_DIR,
) -> Detector:
    """Export bundle folder -> :class:`LiteRtDetector`, else :class:`NullDetector`
    (never raises). The model file must match the sha256 recorded in labels.json."""
    if model_dir is None:
        return NullDetector()
    model, labels_path = model_dir / "model.tflite", model_dir / "labels.json"
    try:
        labels = json.loads(labels_path.read_text(encoding="utf-8"))
        expected = labels["model"].get("sha256")
        if not expected or _sha256(model) != expected:
            return NullDetector()
        return LiteRtDetector(labels, runner_factory(model), directory=directory)
    except Exception:  # noqa: BLE001 - any load failure -> no detector, routes answer 503
        return NullDetector()
