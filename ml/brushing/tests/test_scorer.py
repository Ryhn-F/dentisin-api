from __future__ import annotations

import pytest

import synthetic as syn
from brushing_score import (
    REASONS,
    SEXTANTS,
    BrushingScorer,
    Frame,
    classify_sextant,
    count_reversals,
    score_session,
)
from rules_loader import Rules, load_rules


def dominant(reason_ms: dict[str, int]) -> str:
    return max(reason_ms, key=lambda k: reason_ms[k])


def with_changes(frames: list[Frame], **changes: object) -> list[Frame]:
    return [Frame.from_dict({**f.to_dict(), **changes}) for f in frames]


# ---------------------------------------------------------------- the five required scenarios
def test_idle_face_does_not_count(short_rules: Rules) -> None:
    res = score_session(syn.idle_face(15), short_rules)
    assert res.active_ms == 0
    assert not res.completed
    assert res.enthusiasm_score == 0.0
    assert dominant(res.reason_ms) == "hand_absent"
    assert res.presence_ratio > 0.9


def test_vigorous_brushing_completes_timer(short_rules: Rules) -> None:
    res = score_session(syn.vigorous_brushing(15), short_rules)
    assert res.completed
    assert res.completed_at_ms is not None
    assert 10_000 <= res.completed_at_ms <= 11_500  # 10 s of credit plus short startup lag
    assert res.active_ratio > 0.9
    assert res.enthusiasm_score > 40


def test_hand_waving_away_from_mouth_does_not_count(short_rules: Rules) -> None:
    res = score_session(syn.waving_hand_away(15), short_rules)
    assert res.active_ms == 0
    assert dominant(res.reason_ms) == "hand_far"


def test_static_hand_holding_brush_must_not_count(short_rules: Rules) -> None:
    res = score_session(syn.static_hand_holding_brush(60), short_rules)
    assert res.active_ms == 0
    assert not res.completed
    assert dominant(res.reason_ms) == "low_motion"


def test_face_absent_does_not_count(short_rules: Rules) -> None:
    res = score_session(syn.face_absent(15), short_rules)
    assert res.active_ms == 0
    assert dominant(res.reason_ms) == "face_absent"
    assert res.presence_ratio == 0.0


# ---------------------------------------------------------------- more anti-cheat
def test_one_way_drift_has_variance_but_no_strokes(short_rules: Rules) -> None:
    res = score_session(syn.slow_one_way_drift(5), short_rules)
    assert res.active_ms == 0
    assert dominant(res.reason_ms) == "low_motion"


def test_fast_shaking_rejected(short_rules: Rules) -> None:
    res = score_session(syn.fast_shake(5), short_rules)
    assert res.active_ms == 0
    assert dominant(res.reason_ms) == "too_fast"


def test_motion_variance_threshold_is_configurable() -> None:
    frames = syn.vigorous_brushing(10, amplitude=0.01, noise=0.0)
    lenient = load_rules(overrides={"min_motion_variance": 0.0, "reversal_min_amplitude": 0.002})
    strict = load_rules(overrides={"min_motion_variance": 0.01})
    assert score_session(frames, lenient).active_ms > 5000
    assert score_session(frames, strict).active_ms == 0


def test_low_confidence_face_and_hand(short_rules: Rules) -> None:
    good = syn.vigorous_brushing(5)
    assert score_session(with_changes(good, face_conf=0.1), short_rules).active_ms == 0
    assert score_session(with_changes(good, hand_conf=0.1), short_rules).active_ms == 0


def test_zero_face_scale_treated_as_no_face(short_rules: Rules) -> None:
    frames = with_changes(syn.vigorous_brushing(5), face_scale=0.0)
    assert score_session(frames, short_rules).active_ms == 0


# ---------------------------------------------------------------- timing behaviour
def test_two_minute_timer_with_default_rules(rules: Rules) -> None:
    res = score_session(syn.vigorous_brushing(130), rules)
    assert res.completed
    assert res.completed_at_ms is not None
    assert res.completed_at_ms >= 120_000
    shorter = score_session(syn.vigorous_brushing(60), rules)
    assert not shorter.completed


def test_frame_gap_earns_no_credit_and_resets_window(short_rules: Rules) -> None:
    a = syn.vigorous_brushing(4, seed=1)
    b = syn.vigorous_brushing(4, seed=2, start_ms=a[-1].t_ms + 3000)
    res = score_session(a + b, short_rules)
    assert res.gap_ms >= 3000
    assert res.active_ms < 8000
    assert res.total_ms >= 10_000
    assert res.reason_ms["low_motion"] >= 500  # window restarted after the gap (two warm-ups)


def test_duplicate_and_out_of_order_frames_ignored(short_rules: Rules) -> None:
    frames = syn.vigorous_brushing(5)
    scorer = BrushingScorer(short_rules)
    decisions = [scorer.update(f) for f in frames]
    again = scorer.update(frames[-1])
    older = scorer.update(frames[0])
    assert again.reason == "ignored"
    assert older.reason == "ignored"
    assert not again.active
    res = scorer.result()
    assert res.frames_ignored == 2
    assert res.total_ms == frames[-1].t_ms - frames[0].t_ms
    assert any(d.active for d in decisions)


def test_hand_dropout_grace_bridges_short_occlusion(short_rules: Rules) -> None:
    frames = syn.vigorous_brushing(14, seed=4, dropout_prob=0.2)
    res = score_session(frames, short_rules)
    assert res.reason_ms["grace"] > 0
    assert res.completed


def test_long_hand_loss_is_not_bridged(short_rules: Rules) -> None:
    good = syn.vigorous_brushing(4, seed=1)
    lost = with_changes(
        syn.vigorous_brushing(4, seed=1, start_ms=good[-1].t_ms + 100),
        hand_present=False,
        hand_conf=0.0,
        hand_center=None,
    )
    res = score_session(good + lost, short_rules)
    assert res.reason_ms["hand_absent"] > 2000
    assert res.reason_ms["grace"] <= short_rules.hand_dropout_grace_ms


def test_empty_stream(rules: Rules) -> None:
    res = score_session([], rules)
    assert res.total_ms == 0
    assert res.active_ms == 0
    assert res.active_ratio == 0.0
    assert res.enthusiasm_score == 0.0
    assert not res.completed


def test_single_frame(short_rules: Rules) -> None:
    res = score_session(syn.idle_face(0.1), short_rules)
    assert res.total_ms == 0


# ---------------------------------------------------------------- coverage
@pytest.mark.parametrize("name", SEXTANTS)
def test_each_synthetic_pose_lands_in_its_sextant(name: str, short_rules: Rules) -> None:
    res = score_session(syn.vigorous_brushing(12, sextant=name), short_rules)
    assert res.sextants_covered == [name]
    assert max(res.sextant_ms, key=lambda k: res.sextant_ms[k]) == name


def test_sweep_covers_all_six(short_rules: Rules) -> None:
    res = score_session(syn.brushing_sweep(5), short_rules)
    assert res.sextants_covered == list(SEXTANTS)
    assert res.coverage_fraction == 1.0


def test_classify_sextant_handles_missing_pose(rules: Rules) -> None:
    assert classify_sextant(0.0, -0.5, None, None, rules) == "upper_front"
    assert classify_sextant(0.9, 0.5, None, None, rules) == "lower_right"
    assert classify_sextant(-0.9, 0.5, None, None, rules) == "lower_left"


def test_yaw_shifts_side_when_hand_is_centred(rules: Rules) -> None:
    assert classify_sextant(0.0, 0.1, 20.0, 0.0, rules) == "lower_right"
    assert classify_sextant(0.0, 0.1, -20.0, 0.0, rules) == "lower_left"


# ---------------------------------------------------------------- enthusiasm score
def test_enthusiasm_rises_with_effort(short_rules: Rules) -> None:
    idle = score_session(syn.idle_face(20), short_rules).enthusiasm_score
    short = score_session(syn.vigorous_brushing(4), short_rules).enthusiasm_score
    full = score_session(syn.vigorous_brushing(12), short_rules).enthusiasm_score
    sweep = score_session(syn.brushing_sweep(5), short_rules).enthusiasm_score
    assert idle == 0.0 < short < full < sweep <= 100.0


def test_enthusiasm_vigour_is_capped(short_rules: Rules) -> None:
    mild = score_session(syn.vigorous_brushing(12, freq_hz=1.0, amplitude=0.08), short_rules)
    lively = score_session(syn.vigorous_brushing(12, freq_hz=3.0, amplitude=0.08), short_rules)
    assert mild.mean_active_speed < lively.mean_active_speed
    assert mild.enthusiasm_score < lively.enthusiasm_score
    assert lively.enthusiasm_score <= 100.0


def test_session_result_dict_shape(short_rules: Rules) -> None:
    d = score_session(syn.vigorous_brushing(12), short_rules).to_dict()
    assert set(d["reason_ms"]) == set(REASONS)
    assert d["completed"] is True


# ---------------------------------------------------------------- helpers
def test_count_reversals() -> None:
    assert count_reversals([], 0.01) == 0
    assert count_reversals([0.0], 0.01) == 0
    assert count_reversals([0.0, 0.1, 0.2, 0.3], 0.05) == 0
    assert count_reversals([0.0, 0.1, 0.0, 0.1, 0.0], 0.05) == 3
    assert count_reversals([0.0, -0.1, 0.0, -0.1], 0.05) == 2
    assert count_reversals([0.0, 0.004, 0.0, 0.004, 0.0], 0.05) == 0  # jitter below hysteresis
    assert count_reversals([0.0, 0.1, 0.12, 0.2, 0.18, 0.1], 0.05) == 1


def test_frame_dict_roundtrip() -> None:
    frame = syn.vigorous_brushing(1)[3]
    assert Frame.from_dict(frame.to_dict()) == frame
    absent = syn.face_absent(1)[0]
    assert Frame.from_dict(absent.to_dict()) == absent
    assert absent.to_dict()["mouth_center"] is None


def test_scorer_does_not_retain_frames(short_rules: Rules) -> None:
    scorer = BrushingScorer(short_rules)
    for f in syn.vigorous_brushing(30):
        scorer.update(f)
    # Only a sliding window of (t, x, y) tuples is kept: bounded by window_ms, not session length.
    assert len(scorer._window) <= short_rules.window_ms // 100 + 2
