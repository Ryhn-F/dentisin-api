"""Regenerate ``tests/cases.json``: shared parity cases for the Python reference and the TS port.

Run:  python generate_cases.py            (writes tests/cases.json)

Three sections:
- ``session_cases``: explicit synthetic frame streams + rule overrides + qualitative ``expect``
  (hand-written intent) + ``golden`` (exact output of THIS Python reference; the TS port must
  match within ``float_tolerance``).
- ``checkin_cases``: hand-computed expectations for the window logic (written independently of the
  code under test).
- ``streak_cases``: hand-computed streak counts.

Frames are rounded to 4 decimals BEFORE scoring so the file is self-contained and reproducible.
Everything is synthetic; no real person's data is involved.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import synthetic as syn
from brushing_score import Frame, score_session
from rules_loader import load_rules

CASES_PATH = Path(__file__).parent / "tests" / "cases.json"
# Shorter timer / dwell so cases stay small; everything else comes from brushing_rules.yaml.
OVERRIDES: dict[str, Any] = {"target_duration_s": 5, "min_sextant_ms": 2000}


def _r(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def _round_frame(frame: Frame) -> dict[str, Any]:
    data = frame.to_dict()
    for key in ("face_conf", "face_scale", "head_yaw_deg", "head_pitch_deg", "hand_conf"):
        data[key] = _r(data[key])
    for key in ("mouth_center", "hand_center"):
        if data[key] is not None:
            data[key] = [_r(v) for v in data[key]]
    return data


def _shift(frames: list[Frame], delta_ms: int) -> list[Frame]:
    return [Frame.from_dict({**f.to_dict(), "t_ms": f.t_ms + delta_ms}) for f in frames]


def _session_case(
    case_id: str, description: str, frames: list[Frame], expect: dict[str, Any]
) -> dict[str, Any]:
    rows = [_round_frame(f) for f in frames]
    rules = load_rules(overrides=OVERRIDES)
    golden = score_session([Frame.from_dict(r) for r in rows], rules).to_dict()
    return {
        "id": case_id,
        "description": description,
        "rules_overrides": OVERRIDES,
        "frames": rows,
        "expect": expect,
        "golden": golden,
    }


def _session_cases() -> list[dict[str, Any]]:
    brush_a = syn.vigorous_brushing(4, 10, seed=1)
    brush_b = _shift(syn.vigorous_brushing(4, 10, seed=2), 7000)  # 3 s hole before it
    return [
        _session_case(
            "idle_face",
            "Face visible, no hand: nothing counts.",
            syn.idle_face(8, 10, seed=1),
            {"completed": False, "active_ms_max": 0, "dominant_reason": "hand_absent"},
        ),
        _session_case(
            "vigorous_brushing",
            "Hand oscillating at the mouth for 8 s with a 5 s timer: completes.",
            syn.vigorous_brushing(8, 10, seed=1),
            {"completed": True, "active_ms_min": 5000, "dominant_reason": "active"},
        ),
        _session_case(
            "waving_hand_away",
            "Large hand motion far from the mouth: must NOT count.",
            syn.waving_hand_away(8, 10, seed=1),
            {"completed": False, "active_ms_max": 0, "dominant_reason": "hand_far"},
        ),
        _session_case(
            "static_hand_holding_brush",
            "Hand parked at the mouth with only jitter: must NOT count (anti-cheat).",
            syn.static_hand_holding_brush(8, 10, seed=1),
            {"completed": False, "active_ms_max": 0, "dominant_reason": "low_motion"},
        ),
        _session_case(
            "face_absent",
            "Vigorous hand motion but no face: must NOT count.",
            syn.face_absent(8, 10, seed=1),
            {"completed": False, "active_ms_max": 0, "dominant_reason": "face_absent"},
        ),
        _session_case(
            "slow_one_way_drift",
            "Hand drifting one way at the mouth (no back-and-forth): must NOT count.",
            syn.slow_one_way_drift(5, 10, seed=1),
            {"completed": False, "active_ms_max": 0, "dominant_reason": "low_motion"},
        ),
        _session_case(
            "fast_shake",
            "Shaking faster than plausible brushing: must NOT count.",
            syn.fast_shake(4, 30, seed=1),
            {"completed": False, "active_ms_max": 0, "dominant_reason": "too_fast"},
        ),
        _session_case(
            "sweep_all_sextants",
            "3 s in each sextant: all six covered (coarse coverage).",
            syn.brushing_sweep(3, 10, seed=1),
            {"completed": True, "covered_count": 6, "dominant_reason": "active"},
        ),
        _session_case(
            "brushing_with_hand_dropouts",
            "Brushing with 20% hand-landmark dropouts (brush occlusion): bridged by grace.",
            syn.vigorous_brushing(8, 10, seed=3, dropout_prob=0.2),
            {"completed": True, "active_ms_min": 5000, "dominant_reason": "active"},
        ),
        _session_case(
            "gap_in_stream",
            "4 s brushing, 3 s without frames, 4 s brushing: the gap earns no credit.",
            brush_a + brush_b,
            {"completed": True, "gap_ms_min": 3000, "active_ms_max": 8000},
        ),
        _session_case(
            "duplicate_timestamps_ignored",
            "Repeated frames with the same timestamp are ignored, not double-credited.",
            [f for fr in syn.vigorous_brushing(8, 10, seed=1) for f in (fr, fr)],
            {"completed": True, "frames_ignored_min": 1},
        ),
    ]


def _utc_ms(local_iso: str, tz_name: str) -> int:
    local = datetime.fromisoformat(local_iso).replace(tzinfo=ZoneInfo(tz_name))
    return round(local.timestamp() * 1000)


def _checkin(
    case_id: str,
    tz: str,
    local_iso: str,
    expect_window: str | None,
    expect_date: str | None,
    completed: bool = True,
    windows: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "id": case_id,
        "timezone": tz,
        "local_time": local_iso,
        "windows": windows or DEFAULT_WINDOWS,
        "end_utc_ms": _utc_ms(local_iso, tz),
        "completed": completed,
        "expect": {
            "window": expect_window,
            "local_date": expect_date,
            "counts": completed and expect_window is not None,
        },
    }


DEFAULT_WINDOWS = [
    {"name": "morning", "start": "06:00", "end": "10:00"},
    {"name": "evening", "start": "19:00", "end": "23:00"},
]
LATE_WINDOWS = [
    {"name": "morning", "start": "06:00", "end": "10:00"},
    {"name": "evening", "start": "20:00", "end": "01:00"},  # crosses midnight
]
JKT, MKS, JPR = "Asia/Jakarta", "Asia/Makassar", "Asia/Jayapura"


def _checkin_cases() -> list[dict[str, Any]]:
    d = "2026-10-08"
    return [
        _checkin("before_morning_start", JKT, f"{d}T05:59:59.999", None, None),
        _checkin("morning_start_inclusive", JKT, f"{d}T06:00:00", "morning", d),
        _checkin("morning_last_instant", JKT, f"{d}T09:59:59.999", "morning", d),
        _checkin("morning_end_exclusive", JKT, f"{d}T10:00:00", None, None),
        _checkin("midday_outside", JKT, f"{d}T13:00:00", None, None),
        _checkin("before_evening_start", JKT, f"{d}T18:59:59.999", None, None),
        _checkin("evening_start_inclusive", JKT, f"{d}T19:00:00", "evening", d),
        _checkin("evening_last_instant", JKT, f"{d}T22:59:59.999", "evening", d),
        _checkin("evening_end_exclusive", JKT, f"{d}T23:00:00", None, None),
        _checkin("incomplete_session_does_not_count", JKT, f"{d}T07:00:00", "morning", d, False),
        _checkin("makassar_local_morning", MKS, f"{d}T06:00:00", "morning", d),
        _checkin("jayapura_local_evening", JPR, f"{d}T21:30:00", "evening", d),
        # 06:30 Jakarta on the 9th is still the 8th in UTC: the local date must win.
        _checkin(
            "utc_date_differs_from_local", JKT, "2026-10-09T06:30:00", "morning", "2026-10-09"
        ),
        _checkin(
            "cross_midnight_before_midnight", JKT, f"{d}T23:59:00", "evening", d, True, LATE_WINDOWS
        ),
        _checkin(
            "cross_midnight_exactly_midnight",
            JKT,
            "2026-10-09T00:00:00",
            "evening",
            d,
            True,
            LATE_WINDOWS,
        ),
        _checkin(
            "cross_midnight_after_midnight",
            JKT,
            "2026-10-09T00:30:00",
            "evening",
            d,
            True,
            LATE_WINDOWS,
        ),
        _checkin(
            "cross_midnight_end_exclusive",
            JKT,
            "2026-10-09T01:00:00",
            None,
            None,
            True,
            LATE_WINDOWS,
        ),
        _checkin(
            "cross_midnight_before_start", JKT, f"{d}T19:59:59.999", None, None, True, LATE_WINDOWS
        ),
        _checkin(
            "cross_midnight_start_inclusive", JKT, f"{d}T20:00:00", "evening", d, True, LATE_WINDOWS
        ),
    ]


def _streak_cases() -> list[dict[str, Any]]:
    both = ["morning", "evening"]
    return [
        {
            "id": "today_in_progress_does_not_break",
            "requires": "both",
            "window_names": both,
            "today": "2026-10-08",
            "status": {
                "2026-10-08": ["morning"],
                "2026-10-07": both,
                "2026-10-06": both,
            },
            "expect_streak": 2,
        },
        {
            "id": "today_complete_counts",
            "requires": "both",
            "window_names": both,
            "today": "2026-10-08",
            "status": {"2026-10-08": both, "2026-10-07": both, "2026-10-06": both},
            "expect_streak": 3,
        },
        {
            "id": "yesterday_missing_evening_breaks",
            "requires": "both",
            "window_names": both,
            "today": "2026-10-08",
            "status": {"2026-10-07": ["morning"], "2026-10-06": both},
            "expect_streak": 0,
        },
        {
            "id": "any_window_is_enough",
            "requires": "any",
            "window_names": both,
            "today": "2026-10-08",
            "status": {"2026-10-07": ["morning"], "2026-10-06": ["evening"]},
            "expect_streak": 2,
        },
        {
            "id": "gap_resets",
            "requires": "both",
            "window_names": both,
            "today": "2026-10-08",
            "status": {"2026-10-08": both, "2026-10-06": both},
            "expect_streak": 1,
        },
    ]


def build_cases() -> dict[str, Any]:
    return {
        "schema": "brushing_cases/1",
        "note": (
            "Synthetic data only. 'golden' = output of the Python reference (brushing_score.py) "
            "with brushing_rules.yaml + rules_overrides; the TS port must match within "
            "float_tolerance. 'expect' states intent in plain terms. Regenerate with "
            "`python generate_cases.py`; do not edit by hand."
        ),
        "rules_file": "ml/brushing/brushing_rules.yaml",
        "float_tolerance": 1e-6,
        "session_cases": _session_cases(),
        "checkin_cases": _checkin_cases(),
        "streak_cases": _streak_cases(),
    }


def main() -> None:  # pragma: no cover
    CASES_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(build_cases(), separators=(",", ":"), ensure_ascii=False)
    CASES_PATH.write_text(text + "\n", encoding="utf-8")
    print(f"wrote {CASES_PATH} ({len(text)} bytes)")


if __name__ == "__main__":  # pragma: no cover
    main()
