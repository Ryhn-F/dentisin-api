"""Photo quality gate (FR-21): blur, brightness/exposure and a "teeth visible" HEURISTIC.

Reference implementation for the app port (TypeScript). Design rules for parity:
- Pure functions on uint8 RGB arrays (H, W, 3); no I/O except :func:`load_config`.
- Integer arithmetic wherever possible (luma, downsampling, colour tests); float64 only for
  means/variances, computed two-pass (mean, then mean of squared deviations).
- Deterministic; reference vectors in ``tests/quality_gate_vectors.json`` (inputs described
  parametrically, see ``quality_gate_vectors.py``) must be reproduced by the port.

Pipeline:
1. working image: factor k = max(1, ceil(max(H, W) / max_side)); crop to multiples of k
   (drop right/bottom remainder); each channel block-averaged with integer round-half-up:
   ``(sum + k*k // 2) // (k*k)``.
2. luma Y = (77 R + 150 G + 29 B + 128) >> 8   (integer BT.601 approximation, 0..255).
3. blur: variance of the 3x3 Laplacian [[0,1,0],[1,-4,1],[0,1,0]] over the VALID region
   (no padding); 0.0 if the working image is smaller than 3x3.
4. brightness: mean luma, fraction of pixels with Y <= dark_level, fraction with Y >= bright_level.
5. teeth visible (HEURISTIC, :class:`HeuristicTeethVisibility`): in the central ROI, the
   fraction of "tooth-like" pixels (bright, low saturation) and of "soft-tissue-like" pixels
   (red-dominant: lips, gums). It is NOT a detector of teeth: white paper next to a red object
   passes, and stained/dark teeth may fail. It exists so the interface is in place; replace it
   with a learned estimator (same :class:`TeethVisibilityEstimator` protocol) once labelled
   photos exist. All thresholds are PLACEHOLDER (quality_gate.yaml) until >= 200 labelled photos.

Reason codes (stable order; the app maps them to Indonesian messages): ``too_dark``,
``too_bright``, ``blurry``, ``teeth_not_visible``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt
import yaml

U8 = npt.NDArray[np.uint8]
I64 = npt.NDArray[np.int64]
REASON_ORDER = ("too_dark", "too_bright", "blurry", "teeth_not_visible")
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "quality_gate.yaml"


@dataclass(frozen=True)
class QualityConfig:
    max_side: int
    min_laplacian_var: float
    min_mean: float
    max_mean: float
    dark_level: int
    bright_level: int
    max_dark_fraction: float
    max_bright_fraction: float
    roi_fraction: float
    tooth_min_value: int
    tooth_max_saturation_pct: int
    tissue_min_red_margin: int
    min_tooth_fraction: float
    max_tooth_fraction: float
    min_tissue_fraction: float
    status: str = "PLACEHOLDER"

    @staticmethod
    def from_dict(d: dict[str, Any]) -> QualityConfig:
        b, br, t = d["blur"], d["brightness"], d["teeth_visible"]
        return QualityConfig(
            max_side=int(d["working_size"]["max_side"]),
            min_laplacian_var=float(b["min_laplacian_var"]),
            min_mean=float(br["min_mean"]),
            max_mean=float(br["max_mean"]),
            dark_level=int(br["dark_level"]),
            bright_level=int(br["bright_level"]),
            max_dark_fraction=float(br["max_dark_fraction"]),
            max_bright_fraction=float(br["max_bright_fraction"]),
            roi_fraction=float(t["roi_fraction"]),
            tooth_min_value=int(t["tooth_min_value"]),
            tooth_max_saturation_pct=int(t["tooth_max_saturation_pct"]),
            tissue_min_red_margin=int(t["tissue_min_red_margin"]),
            min_tooth_fraction=float(t["min_tooth_fraction"]),
            max_tooth_fraction=float(t["max_tooth_fraction"]),
            min_tissue_fraction=float(t["min_tissue_fraction"]),
            status=str(d.get("status", "PLACEHOLDER")),
        )


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> QualityConfig:
    return QualityConfig.from_dict(yaml.safe_load(path.read_text(encoding="utf-8")))


# ------------------------------------------------------------------------------ primitives


def _check_rgb(rgb: npt.NDArray[Any]) -> None:
    if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype != np.uint8:
        raise ValueError(f"expected uint8 array (H, W, 3), got {rgb.dtype} {rgb.shape}")
    if rgb.shape[0] < 1 or rgb.shape[1] < 1:
        raise ValueError("empty image")


def working_factor(height: int, width: int, max_side: int) -> int:
    return max(1, -(-max(height, width) // max_side))  # ceil division


def block_downsample(rgb: U8, k: int) -> U8:
    """Integer block mean with round-half-up; drops the right/bottom remainder."""
    if k == 1:
        return rgb
    h, w = (rgb.shape[0] // k) * k, (rgb.shape[1] // k) * k
    if h == 0 or w == 0:
        raise ValueError("image smaller than the downsample factor")
    blocks = rgb[:h, :w].astype(np.int64).reshape(h // k, k, w // k, k, 3).sum(axis=(1, 3))
    return ((blocks + (k * k) // 2) // (k * k)).astype(np.uint8)


def luma(rgb: U8) -> I64:
    x = rgb.astype(np.int64)
    out: I64 = (77 * x[..., 0] + 150 * x[..., 1] + 29 * x[..., 2] + 128) >> 8
    return out


def laplacian_variance(gray: I64) -> float:
    if gray.shape[0] < 3 or gray.shape[1] < 3:
        return 0.0
    g = gray.astype(np.int64)
    lap = g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:] - 4 * g[1:-1, 1:-1]
    v = lap.astype(np.float64)
    mean = float(v.mean())
    return float(((v - mean) ** 2).mean())


def brightness_stats(gray: I64, dark_level: int, bright_level: int) -> tuple[float, float, float]:
    n = gray.size
    return float(gray.astype(np.float64).mean()), float(np.count_nonzero(gray <= dark_level) / n), float(np.count_nonzero(gray >= bright_level) / n)


def central_roi(arr: npt.NDArray[Any], fraction: float) -> npt.NDArray[Any]:
    """Middle ``fraction`` of height and width: offset = floor(size * (1 - fraction) / 2) on each side."""
    h, w = arr.shape[0], arr.shape[1]
    dy, dx = int(h * (1.0 - fraction) // 2), int(w * (1.0 - fraction) // 2)
    return arr[dy : h - dy, dx : w - dx]


# ---------------------------------------------------------------------------- teeth visible


@dataclass(frozen=True)
class TeethVisibility:
    visible: bool
    score: float
    method: str
    details: dict[str, float] = field(default_factory=dict)


class TeethVisibilityEstimator(Protocol):
    """Interface for the "teeth visible" check. A learned model must implement this."""

    def estimate(self, rgb_working: U8, cfg: QualityConfig) -> TeethVisibility: ...


class HeuristicTeethVisibility:
    """Colour heuristic (see module docstring). NOT validated; documented as a heuristic."""

    method = "heuristic_color_v1"

    def estimate(self, rgb_working: U8, cfg: QualityConfig) -> TeethVisibility:
        roi = central_roi(rgb_working, cfg.roi_fraction).astype(np.int64)
        r, g, b = roi[..., 0], roi[..., 1], roi[..., 2]
        mx = np.maximum(np.maximum(r, g), b)
        mn = np.minimum(np.minimum(r, g), b)
        tooth = (mx >= cfg.tooth_min_value) & ((mx - mn) * 100 <= cfg.tooth_max_saturation_pct * mx)
        tissue = (r - np.maximum(g, b)) >= cfg.tissue_min_red_margin
        n = max(1, tooth.size)
        tf, sf = float(np.count_nonzero(tooth) / n), float(np.count_nonzero(tissue) / n)
        visible = cfg.min_tooth_fraction <= tf <= cfg.max_tooth_fraction and sf >= cfg.min_tissue_fraction
        return TeethVisibility(visible, tf, self.method, {"tooth_fraction": tf, "tissue_fraction": sf})


# ------------------------------------------------------------------------------------- gate


@dataclass(frozen=True)
class QualityResult:
    passed: bool
    reasons: tuple[str, ...]
    metrics: dict[str, float | int | str]
    config_status: str


def evaluate_quality(rgb: U8, cfg: QualityConfig, teeth: TeethVisibilityEstimator | None = None) -> QualityResult:
    _check_rgb(rgb)
    k = working_factor(rgb.shape[0], rgb.shape[1], cfg.max_side)
    work = block_downsample(rgb, k)
    y = luma(work)
    lap = laplacian_variance(y)
    mean, dark, bright = brightness_stats(y, cfg.dark_level, cfg.bright_level)
    tv = (teeth or HeuristicTeethVisibility()).estimate(work, cfg)
    reasons: list[str] = []
    if mean < cfg.min_mean or dark > cfg.max_dark_fraction:
        reasons.append("too_dark")
    if mean > cfg.max_mean or bright > cfg.max_bright_fraction:
        reasons.append("too_bright")
    if lap < cfg.min_laplacian_var:
        reasons.append("blurry")
    if not tv.visible:
        reasons.append("teeth_not_visible")
    metrics: dict[str, float | int | str] = {
        "factor": k,
        "working_width": int(work.shape[1]),
        "working_height": int(work.shape[0]),
        "laplacian_var": lap,
        "mean_luma": mean,
        "dark_fraction": dark,
        "bright_fraction": bright,
        "teeth_score": tv.score,
        "teeth_method": tv.method,
        **tv.details,
    }
    ordered = tuple(r for r in REASON_ORDER if r in reasons)
    return QualityResult(not ordered, ordered, metrics, cfg.status)
