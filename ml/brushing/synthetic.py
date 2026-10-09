"""Synthetic landmark-feature streams for tests (no camera, no images, no real children's data).

Each generator is deterministic for a given seed and returns ``Frame`` objects in time order.
Poses are nominal and only meant to exercise the reference scorer; they say nothing about how
real children brush.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

from brushing_score import SEXTANTS, Frame

MOUTH = (0.5, 0.55)  # mirrored-preview coordinates
FACE_SCALE = 0.45  # face height in normalised image units


@dataclass(frozen=True)
class Pose:
    """Mean hand offset from the mouth (face heights) and head angles for one sextant."""

    rel_x: float
    rel_y: float
    yaw_deg: float
    pitch_deg: float


SEXTANT_POSES: dict[str, Pose] = {
    "upper_left": Pose(-0.35, -0.15, -15.0, 0.0),
    "upper_front": Pose(0.0, -0.15, 0.0, 0.0),
    "upper_right": Pose(0.35, -0.15, 15.0, 0.0),
    "lower_left": Pose(-0.35, 0.25, -15.0, 0.0),
    "lower_front": Pose(0.0, 0.25, 0.0, 0.0),
    "lower_right": Pose(0.35, 0.25, 15.0, 0.0),
}
assert set(SEXTANT_POSES) == set(SEXTANTS)


def _frame(
    t_ms: int,
    *,
    face: bool = True,
    hand_rel: tuple[float, float] | None = None,
    pose: Pose | None = None,
    face_conf: float = 0.95,
    hand_conf: float = 0.9,
) -> Frame:
    yaw = None if not face else (pose.yaw_deg if pose else 0.0)
    pitch = None if not face else (pose.pitch_deg if pose else 0.0)
    hand_center = (
        None
        if hand_rel is None
        else (MOUTH[0] + hand_rel[0] * FACE_SCALE, MOUTH[1] + hand_rel[1] * FACE_SCALE)
    )
    return Frame(
        t_ms=t_ms,
        face_present=face,
        face_conf=face_conf if face else 0.0,
        face_scale=FACE_SCALE if face else None,
        mouth_center=MOUTH if face else None,
        head_yaw_deg=yaw,
        head_pitch_deg=pitch,
        hand_present=hand_rel is not None,
        hand_conf=hand_conf if hand_rel is not None else 0.0,
        hand_center=hand_center,
    )


def _times(duration_s: float, fps: int, start_ms: int) -> list[int]:
    count = int(duration_s * fps)
    return [start_ms + round(i * 1000 / fps) for i in range(count)]


def idle_face(
    duration_s: float = 10, fps: int = 10, seed: int = 0, start_ms: int = 0
) -> list[Frame]:
    """Face visible, no hand."""
    rng = random.Random(seed)
    return [
        _frame(t, pose=Pose(0.0, 0.0, rng.gauss(0, 1.0), rng.gauss(0, 1.0)))
        for t in _times(duration_s, fps, start_ms)
    ]


def vigorous_brushing(
    duration_s: float = 10,
    fps: int = 10,
    seed: int = 0,
    start_ms: int = 0,
    sextant: str = "upper_front",
    freq_hz: float = 3.0,
    amplitude: float = 0.08,
    noise: float = 0.003,
    dropout_prob: float = 0.0,
) -> list[Frame]:
    """Hand oscillating back and forth at the mouth. ``dropout_prob`` drops hand landmarks."""
    rng = random.Random(seed)
    pose = SEXTANT_POSES[sextant]
    frames = []
    for t in _times(duration_s, fps, start_ms):
        phase = 2 * math.pi * freq_hz * (t - start_ms) / 1000.0
        rel = (
            pose.rel_x + amplitude * math.sin(phase) + rng.gauss(0, noise),
            pose.rel_y + 0.4 * amplitude * math.cos(phase) + rng.gauss(0, noise),
        )
        dropped = rng.random() < dropout_prob
        frames.append(_frame(t, hand_rel=None if dropped else rel, pose=pose))
    return frames


def brushing_sweep(
    seconds_per_sextant: float = 10,
    fps: int = 10,
    seed: int = 0,
    order: Sequence[str] = SEXTANTS,
) -> list[Frame]:
    """Vigorous brushing that visits each sextant in turn (contiguous timestamps)."""
    frames: list[Frame] = []
    start = 0
    for i, name in enumerate(order):
        seg = vigorous_brushing(seconds_per_sextant, fps, seed + i, start, sextant=name)
        frames.extend(seg)
        start = seg[-1].t_ms + round(1000 / fps)
    return frames


def waving_hand_away(
    duration_s: float = 10, fps: int = 10, seed: int = 0, start_ms: int = 0
) -> list[Frame]:
    """Big hand motion far from the mouth (e.g. waving at the camera)."""
    rng = random.Random(seed)
    frames = []
    for t in _times(duration_s, fps, start_ms):
        phase = 2 * math.pi * 2.0 * (t - start_ms) / 1000.0
        rel = (1.8 + 0.5 * math.sin(phase), -0.3 + 0.2 * math.cos(phase) + rng.gauss(0, 0.005))
        frames.append(_frame(t, hand_rel=rel))
    return frames


def static_hand_holding_brush(
    duration_s: float = 10, fps: int = 10, seed: int = 0, start_ms: int = 0, noise: float = 0.002
) -> list[Frame]:
    """Hand parked at the mouth; only landmark jitter. Must NOT count (anti-cheat)."""
    rng = random.Random(seed)
    pose = SEXTANT_POSES["upper_front"]
    return [
        _frame(
            t,
            hand_rel=(0.4 + rng.gauss(0, noise), 0.2 + rng.gauss(0, noise)),
            pose=pose,
        )
        for t in _times(duration_s, fps, start_ms)
    ]


def slow_one_way_drift(
    duration_s: float = 5, fps: int = 10, seed: int = 0, start_ms: int = 0
) -> list[Frame]:
    """Hand sliding steadily in one direction at the mouth: variance but no back-and-forth."""
    rng = random.Random(seed)
    frames = []
    n = int(duration_s * fps)
    for i, t in enumerate(_times(duration_s, fps, start_ms)):
        rel = (-0.3 + 0.6 * i / max(1, n - 1) + rng.gauss(0, 0.001), 0.1 + rng.gauss(0, 0.001))
        frames.append(_frame(t, hand_rel=rel))
    return frames


def fast_shake(
    duration_s: float = 5, fps: int = 30, seed: int = 0, start_ms: int = 0, freq_hz: float = 8.0
) -> list[Frame]:
    """Violent shaking at the mouth: faster than brushing is plausible."""
    rng = random.Random(seed)
    frames = []
    for t in _times(duration_s, fps, start_ms):
        phase = 2 * math.pi * freq_hz * (t - start_ms) / 1000.0
        frames.append(_frame(t, hand_rel=(0.3 * math.sin(phase), 0.1 + rng.gauss(0, 0.002))))
    return frames


def face_absent(
    duration_s: float = 10, fps: int = 10, seed: int = 0, start_ms: int = 0
) -> list[Frame]:
    """Vigorous hand motion but no face (e.g. brushing off-camera or holding the phone wrong)."""
    rng = random.Random(seed)
    frames = []
    for t in _times(duration_s, fps, start_ms):
        phase = 2 * math.pi * 3.0 * (t - start_ms) / 1000.0
        rel = (0.1 * math.sin(phase) + rng.gauss(0, 0.003), 0.05)
        frames.append(_frame(t, face=False, hand_rel=rel))
    return frames
