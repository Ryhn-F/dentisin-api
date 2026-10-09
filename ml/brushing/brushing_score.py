"""Reference rule-based brushing scorer operating on landmark FEATURE streams (PRD FR-60..64).

Scope (ADR 003 sec.4): duration + presence + coarse sextant coverage + an "enthusiasm" game score.
This is NOT clinical technique scoring and nothing here is validated against observers or a dentist.

Design constraints:
- Input is a stream of per-frame feature records (see ``Frame``), never images. The scorer keeps
  only a short sliding window of hand offsets (no frames, no video) so it can run in memory.
- Pure Python, deterministic, integer millisecond clock, thresholds all from ``Rules``
  (``brushing_rules.yaml``) so it ports 1:1 to TypeScript and shares ``tests/cases.json``.

Coordinates: normalised image units of the mirrored front-camera preview (x right, y down).
Hand offsets are relative to the mouth centre and divided by ``face_scale`` (face height).
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

from rules_loader import Rules

SEXTANTS = (
    "upper_left",
    "upper_front",
    "upper_right",
    "lower_left",
    "lower_front",
    "lower_right",
)
# Reasons a frame interval is (not) credited. "grace" = brief hand-landmark dropout bridged.
REASONS = (
    "active",
    "grace",
    "face_absent",
    "hand_absent",
    "hand_far",
    "low_motion",
    "too_fast",
)

Point = tuple[float, float]


@dataclass(frozen=True)
class Frame:
    """One frame of landmark-derived features. No pixels, no raw face mesh."""

    t_ms: int
    face_present: bool
    face_conf: float
    face_scale: float | None
    mouth_center: Point | None
    head_yaw_deg: float | None
    head_pitch_deg: float | None
    hand_present: bool
    hand_conf: float
    hand_center: Point | None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Frame:
        def point(key: str) -> Point | None:
            raw = data.get(key)
            return None if raw is None else (float(raw[0]), float(raw[1]))

        def opt_float(key: str) -> float | None:
            raw = data.get(key)
            return None if raw is None else float(raw)

        return cls(
            t_ms=int(data["t_ms"]),
            face_present=bool(data["face_present"]),
            face_conf=float(data["face_conf"]),
            face_scale=opt_float("face_scale"),
            mouth_center=point("mouth_center"),
            head_yaw_deg=opt_float("head_yaw_deg"),
            head_pitch_deg=opt_float("head_pitch_deg"),
            hand_present=bool(data["hand_present"]),
            hand_conf=float(data["hand_conf"]),
            hand_center=point("hand_center"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "t_ms": self.t_ms,
            "face_present": self.face_present,
            "face_conf": self.face_conf,
            "face_scale": self.face_scale,
            "mouth_center": None if self.mouth_center is None else list(self.mouth_center),
            "head_yaw_deg": self.head_yaw_deg,
            "head_pitch_deg": self.head_pitch_deg,
            "hand_present": self.hand_present,
            "hand_conf": self.hand_conf,
            "hand_center": None if self.hand_center is None else list(self.hand_center),
        }


@dataclass(frozen=True)
class FrameDecision:
    """Per-frame outcome, useful for live UI hints ("Wajahmu belum terlihat")."""

    active: bool
    reason: str
    sextant: str | None


@dataclass
class SessionResult:
    total_ms: int
    active_ms: int
    completed: bool
    completed_at_ms: int | None
    active_ratio: float
    presence_ratio: float
    sextant_ms: dict[str, int]
    sextants_covered: list[str]
    coverage_fraction: float
    mean_active_speed: float
    enthusiasm_score: float
    reason_ms: dict[str, int]
    frames_ignored: int = 0
    gap_ms: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly, rounded dict used for golden values in tests/cases.json."""
        return {
            "total_ms": self.total_ms,
            "active_ms": self.active_ms,
            "completed": self.completed,
            "completed_at_ms": self.completed_at_ms,
            "active_ratio": round(self.active_ratio, 6),
            "presence_ratio": round(self.presence_ratio, 6),
            "sextant_ms": dict(self.sextant_ms),
            "sextants_covered": list(self.sextants_covered),
            "coverage_fraction": round(self.coverage_fraction, 6),
            "mean_active_speed": round(self.mean_active_speed, 6),
            "enthusiasm_score": round(self.enthusiasm_score, 1),
            "reason_ms": dict(self.reason_ms),
            "frames_ignored": self.frames_ignored,
            "gap_ms": self.gap_ms,
        }


def count_reversals(values: list[float], min_amplitude: float) -> int:
    """Count direction reversals of a 1-D signal, ignoring wiggles below ``min_amplitude``."""
    if len(values) < 2:
        return 0
    count = 0
    direction = 0
    extreme = values[0]
    for v in values[1:]:
        if direction == 0:
            if abs(v - extreme) >= min_amplitude:
                direction = 1 if v > extreme else -1
                extreme = v
        elif direction == 1:
            if v > extreme:
                extreme = v
            elif extreme - v >= min_amplitude:
                direction = -1
                count += 1
                extreme = v
        elif v < extreme:
            extreme = v
        elif v - extreme >= min_amplitude:
            direction = 1
            count += 1
            extreme = v
    return count


def classify_sextant(
    mean_x: float, mean_y: float, yaw_deg: float | None, pitch_deg: float | None, rules: Rules
) -> str:
    """Coarse sextant guess. Heuristic and unvalidated: a game guide, not a clinical measurement.

    Left/right are as seen on the mirrored preview. Positive yaw = head turned toward the right of
    the preview; positive pitch = chin down; positive y = hand below the mouth centre.
    """
    h = rules.hand_x_weight * mean_x + rules.head_yaw_weight * (
        (yaw_deg or 0.0) / rules.yaw_ref_deg
    )
    v = rules.hand_y_weight * mean_y + rules.head_pitch_weight * (
        (pitch_deg or 0.0) / rules.pitch_ref_deg
    )
    column = (
        "right"
        if h > rules.h_side_threshold
        else "left"
        if h < -rules.h_side_threshold
        else "front"
    )
    row = "upper" if v < rules.v_split else "lower"
    return f"{row}_{column}"


class BrushingScorer:
    """Streaming scorer. Feed frames in time order with ``update``; read ``result`` any time."""

    def __init__(self, rules: Rules) -> None:
        self._rules = rules
        self._window: deque[tuple[int, float, float]] = deque()  # (t_ms, rel_x, rel_y)
        self._first_t: int | None = None
        self._prev_t: int | None = None
        self._last_active_t: int | None = None
        self._last_sextant: str | None = None
        self._active_ms = 0
        self._face_ms = 0
        self._speed_weighted = 0.0
        self._speed_ms = 0
        self._completed_at: int | None = None
        self._sextant_ms = dict.fromkeys(SEXTANTS, 0)
        self._reason_ms = dict.fromkeys(REASONS, 0)
        self._ignored = 0
        self._gap_ms = 0

    # ------------------------------------------------------------------ public API
    def update(self, frame: Frame) -> FrameDecision:
        rules = self._rules
        t = frame.t_ms
        if self._prev_t is not None and t <= self._prev_t:
            self._ignored += 1  # duplicate / out-of-order timestamp: never credit time twice
            return FrameDecision(False, "ignored", None)

        dt = 0
        if self._prev_t is not None:
            dt = t - self._prev_t
            if dt > rules.max_frame_gap_ms:
                self._gap_ms += dt
                dt = 0
                self._window.clear()
                self._last_active_t = None
        if self._first_t is None:
            self._first_t = t
        self._prev_t = t

        decision, speed = self._decide(frame)
        self._credit(frame, decision, speed, dt)
        return decision

    def result(self) -> SessionResult:
        rules = self._rules
        total = 0 if self._first_t is None or self._prev_t is None else self._prev_t - self._first_t
        target_ms = rules.target_duration_ms
        covered = [s for s in SEXTANTS if self._sextant_ms[s] >= rules.min_sextant_ms]
        coverage = len(covered) / len(SEXTANTS)
        mean_speed = self._speed_weighted / self._speed_ms if self._speed_ms else 0.0
        duration_c = min(1.0, self._active_ms / target_ms)
        vigor_c = _clip(
            (mean_speed - rules.vigor_min_speed) / (rules.vigor_max_speed - rules.vigor_min_speed),
            0.0,
            1.0,
        )
        weight_sum = rules.w_duration + rules.w_vigor + rules.w_coverage
        score = (
            0.0
            if self._active_ms == 0
            else 100.0
            * (
                rules.w_duration * duration_c
                + rules.w_vigor * vigor_c
                + rules.w_coverage * coverage
            )
            / weight_sum
        )
        return SessionResult(
            total_ms=total,
            active_ms=self._active_ms,
            completed=self._completed_at is not None,
            completed_at_ms=self._completed_at,
            active_ratio=self._active_ms / total if total else 0.0,
            presence_ratio=self._face_ms / total if total else 0.0,
            sextant_ms=dict(self._sextant_ms),
            sextants_covered=covered,
            coverage_fraction=coverage,
            mean_active_speed=mean_speed,
            enthusiasm_score=score,
            reason_ms=dict(self._reason_ms),
            frames_ignored=self._ignored,
            gap_ms=self._gap_ms,
        )

    # ------------------------------------------------------------------ internals
    def _decide(self, frame: Frame) -> tuple[FrameDecision, float]:
        rules = self._rules
        face_ok = (
            frame.face_present
            and frame.face_conf >= rules.min_face_conf
            and frame.face_scale is not None
            and frame.face_scale > 0
            and frame.mouth_center is not None
        )
        if not face_ok:
            return FrameDecision(False, "face_absent", None), 0.0
        assert frame.face_scale is not None  # narrowing for mypy; guaranteed by face_ok
        assert frame.mouth_center is not None

        hand_ok = (
            frame.hand_present
            and frame.hand_conf >= rules.min_hand_conf
            and frame.hand_center is not None
        )
        if not hand_ok:
            return self._grace_or(frame, "hand_absent"), 0.0
        assert frame.hand_center is not None

        rel_x = (frame.hand_center[0] - frame.mouth_center[0]) / frame.face_scale
        rel_y = (frame.hand_center[1] - frame.mouth_center[1]) / frame.face_scale
        if math.hypot(rel_x, rel_y) > rules.near_mouth_max_rel:
            return FrameDecision(False, "hand_far", None), 0.0

        t = frame.t_ms
        self._window.append((t, rel_x, rel_y))
        while self._window and self._window[0][0] < t - rules.window_ms:
            self._window.popleft()
        if len(self._window) < rules.min_window_samples:
            return FrameDecision(False, "low_motion", None), 0.0

        xs = [w[1] for w in self._window]
        ys = [w[2] for w in self._window]
        n = len(xs)
        mean_x = sum(xs) / n
        mean_y = sum(ys) / n
        variance = sum((x - mean_x) ** 2 for x in xs) / n + sum((y - mean_y) ** 2 for y in ys) / n
        speed = self._window_speed()
        if speed > rules.max_mean_speed:
            return FrameDecision(False, "too_fast", None), speed
        reversals = count_reversals(xs, rules.reversal_min_amplitude) + count_reversals(
            ys, rules.reversal_min_amplitude
        )
        if variance < rules.min_motion_variance or reversals < rules.min_direction_reversals:
            return FrameDecision(False, "low_motion", None), speed

        sextant = classify_sextant(mean_x, mean_y, frame.head_yaw_deg, frame.head_pitch_deg, rules)
        return FrameDecision(True, "active", sextant), speed

    def _grace_or(self, frame: Frame, reason: str) -> FrameDecision:
        """Bridge a short hand-landmark dropout (brush occlusion) while the face is still there."""
        last = self._last_active_t
        if last is not None and frame.t_ms - last <= self._rules.hand_dropout_grace_ms:
            return FrameDecision(True, "grace", self._last_sextant)
        return FrameDecision(False, reason, None)

    def _window_speed(self) -> float:
        samples = list(self._window)
        total = 0.0
        steps = 0
        for (t0, x0, y0), (t1, x1, y1) in pairwise(samples):
            seconds = (t1 - t0) / 1000.0
            if seconds > 0:
                total += math.hypot(x1 - x0, y1 - y0) / seconds
                steps += 1
        return total / steps if steps else 0.0

    def _credit(self, frame: Frame, decision: FrameDecision, speed: float, dt: int) -> None:
        if dt <= 0:  # first frame or first frame after a gap: nothing to credit yet
            return
        if decision.reason != "face_absent":
            self._face_ms += dt
        self._reason_ms[decision.reason] += dt
        if not decision.active:
            return
        self._active_ms += dt
        if decision.sextant is not None:
            self._sextant_ms[decision.sextant] += dt
        if decision.reason == "active":
            self._last_active_t = frame.t_ms
            self._last_sextant = decision.sextant
            self._speed_weighted += speed * dt
            self._speed_ms += dt
        if self._completed_at is None and self._active_ms >= self._rules.target_duration_ms:
            self._completed_at = frame.t_ms


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def score_session(frames: Iterable[Frame], rules: Rules) -> SessionResult:
    """Score a whole recorded stream (convenience wrapper over ``BrushingScorer``)."""
    scorer = BrushingScorer(rules)
    for frame in frames:
        scorer.update(frame)
    return scorer.result()
