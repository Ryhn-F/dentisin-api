"""Runs the shared tests/cases.json (the same file the TypeScript port must pass)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from brushing_score import Frame, score_session
from checkin import current_streak, evaluate_checkin, parse_windows
from generate_cases import CASES_PATH, build_cases
from rules_loader import load_rules

CASES: dict[str, Any] = json.loads(CASES_PATH.read_text(encoding="utf-8"))
TOL = CASES["float_tolerance"]


def assert_close(actual: Any, expected: Any, path: str = "") -> None:
    if isinstance(expected, dict):
        assert set(actual) == set(expected), path
        for key in expected:
            assert_close(actual[key], expected[key], f"{path}.{key}")
    elif isinstance(expected, list):
        assert len(actual) == len(expected), path
        for i, (a, e) in enumerate(zip(actual, expected, strict=True)):
            assert_close(a, e, f"{path}[{i}]")
    elif isinstance(expected, float):
        assert actual == pytest.approx(expected, abs=TOL), path
    else:
        assert actual == expected, path


def test_cases_file_is_up_to_date() -> None:
    """Fails if the generator or synthetic streams changed without regenerating cases.json."""
    assert build_cases() == CASES, "run `python generate_cases.py` and review the diff"


def test_case_ids_unique_and_nonempty() -> None:
    for section in ("session_cases", "checkin_cases", "streak_cases"):
        ids = [c["id"] for c in CASES[section]]
        assert ids, section
        assert len(ids) == len(set(ids)), section


@pytest.mark.parametrize("case", CASES["session_cases"], ids=lambda c: c["id"])
def test_session_case(case: dict[str, Any]) -> None:
    rules = load_rules(overrides=case["rules_overrides"])
    result = score_session([Frame.from_dict(f) for f in case["frames"]], rules).to_dict()

    assert_close(result, case["golden"])  # exact parity with the reference

    expect = case["expect"]  # plain-language intent, checked independently of the golden values
    if "completed" in expect:
        assert result["completed"] is expect["completed"]
    if "active_ms_min" in expect:
        assert result["active_ms"] >= expect["active_ms_min"]
    if "active_ms_max" in expect:
        assert result["active_ms"] <= expect["active_ms_max"]
    if "dominant_reason" in expect:
        reasons = result["reason_ms"]
        assert max(reasons, key=lambda k: reasons[k]) == expect["dominant_reason"]
    if "covered_count" in expect:
        assert len(result["sextants_covered"]) == expect["covered_count"]
    if "gap_ms_min" in expect:
        assert result["gap_ms"] >= expect["gap_ms_min"]
    if "frames_ignored_min" in expect:
        assert result["frames_ignored"] >= expect["frames_ignored_min"]


@pytest.mark.parametrize("case", CASES["checkin_cases"], ids=lambda c: c["id"])
def test_checkin_case(case: dict[str, Any]) -> None:
    windows = parse_windows(case["windows"])
    res = evaluate_checkin(case["end_utc_ms"], case["completed"], windows, case["timezone"])
    expect = case["expect"]
    assert res.window == expect["window"]
    assert (res.local_date.isoformat() if res.local_date else None) == expect["local_date"]
    assert res.counts is expect["counts"]


@pytest.mark.parametrize("case", CASES["streak_cases"], ids=lambda c: c["id"])
def test_streak_case(case: dict[str, Any]) -> None:
    status = {date.fromisoformat(k): set(v) for k, v in case["status"].items()}
    today = date.fromisoformat(case["today"])
    streak = current_streak(status, today, case["window_names"], case["requires"])
    assert streak == case["expect_streak"]


def test_cases_contain_only_landmark_features() -> None:
    allowed = set(CASES["session_cases"][0]["frames"][0])
    for case in CASES["session_cases"]:
        for frame in case["frames"]:
            assert set(frame) == allowed  # no image-like or extra fields
    assert Path(CASES_PATH).name == "cases.json"
