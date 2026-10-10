"""Hypothesis safety properties of the engine (run against every test params profile)."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from triage import TriageInputError, evaluate, resolve_params
from triage.forbidden import find_forbidden, load_forbidden
from triage.loader import load_rules, read_json
from triage.paths import PROFILES_PATH

RULES = load_rules()
FORBIDDEN = load_forbidden()
PROFILES = read_json(PROFILES_PATH)["profiles"]
PARAMS = {name: resolve_params(RULES, p["values"]) for name, p in PROFILES.items()}
RANK = {lvl["id"]: lvl["rank"] for lvl in RULES["levels"]}
TOP = max(RANK, key=lambda level_id: RANK[level_id])
QUESTIONS = [q["id"] for q in RULES["questions"]]
RED_FLAGS = [q["id"] for q in RULES["questions"] if q["group"] == "red_flag"]
CLASSES = [c["id"] for c in RULES["detection_classes"]]
ANSWER_ORDER = ["no", "unknown", "yes"]  # "unknown" is never safer than "no"
SETTINGS = settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])


# ADR 014: detections are {class, confidence} only; geometry is not an engine input.
detections = st.fixed_dictionaries(
    {"class": st.sampled_from(CLASSES), "confidence": st.floats(0, 1)}
)
OTHER_CLASS = {CLASSES[0]: CLASSES[1], CLASSES[1]: CLASSES[0]}


@st.composite
def inputs(draw: st.DrawFn) -> tuple[str, dict[str, Any]]:
    profile = draw(st.sampled_from(sorted(PROFILES)))
    assessed = draw(st.booleans())
    return profile, {
        "detections": draw(st.lists(detections, max_size=6)) if assessed else [],
        "photo_status": "assessed"
        if assessed
        else draw(st.sampled_from(["not_taken", "quality_failed"])),
        "symptoms": {q: draw(st.sampled_from(ANSWER_ORDER)) for q in QUESTIONS},
        "age_months": draw(st.none() | st.integers(0, 300)),
        "pain": draw(st.just("unknown") | st.integers(0, 10)),
    }


def level(profile: str, data: dict[str, Any]) -> int:
    rank: int = RANK[evaluate(RULES, data, PARAMS[profile])["level"]]
    return rank


@SETTINGS
@given(inputs(), st.sampled_from(RED_FLAGS))
def test_adding_a_red_flag_never_lowers_level_and_always_wins(
    case: tuple[str, dict[str, Any]], flag: str
) -> None:
    profile, data = case
    before = level(profile, data)
    data2 = copy.deepcopy(data)
    data2["symptoms"][flag] = "yes"
    after = level(profile, data2)
    assert after >= before
    assert after == RANK[TOP]


@SETTINGS
@given(inputs(), st.sampled_from(QUESTIONS))
def test_answers_are_monotone_no_unknown_yes(case: tuple[str, dict[str, Any]], q: str) -> None:
    profile, data = case
    levels = []
    for answer in ANSWER_ORDER:
        data2 = copy.deepcopy(data)
        data2["symptoms"][q] = answer
        levels.append(level(profile, data2))
    assert levels == sorted(levels)


@SETTINGS
@given(inputs(), detections)
def test_adding_a_lesion_never_lowers_level(
    case: tuple[str, dict[str, Any]], det: dict[str, Any]
) -> None:
    profile, data = case
    if data["photo_status"] != "assessed":
        data["photo_status"] = "assessed"
    before = level(profile, data)
    data["detections"] = [*data["detections"], det]
    assert level(profile, data) >= before


@SETTINGS
@given(inputs())
def test_dentition_never_changes_the_level(case: tuple[str, dict[str, Any]]) -> None:
    """Same count of counted findings -> same level, whether primary or permanent teeth."""
    profile, data = case
    data["photo_status"] = "assessed"
    sure = [{"class": d["class"], "confidence": 1.0} for d in data["detections"]]
    flipped = [{"class": OTHER_CLASS[d["class"]], "confidence": 1.0} for d in sure]
    assert level(profile, {**data, "detections": sure}) == level(
        profile, {**data, "detections": flipped}
    )


@SETTINGS
@given(inputs(), st.integers(0, 5), st.floats(0.0, 1.0))
def test_level_monotone_in_confidence(
    case: tuple[str, dict[str, Any]], idx: int, grow: float
) -> None:
    profile, data = case
    data["photo_status"] = "assessed"
    if not data["detections"]:
        data["detections"] = [{"class": CLASSES[0], "confidence": 0.3}]
    det = data["detections"][idx % len(data["detections"])]
    before = level(profile, data)
    det["confidence"] = det["confidence"] + (1 - det["confidence"]) * grow
    assert level(profile, data) >= before


@SETTINGS
@given(inputs(), st.integers(0, 5), st.dictionaries(st.text(min_size=1), st.floats(0, 1)))
def test_box_geometry_can_never_reach_the_engine(
    case: tuple[str, dict[str, Any]], idx: int, geometry: dict[str, float]
) -> None:
    """Any extra detection key (e.g. a box) is rejected, so box size cannot affect the level."""
    profile, data = case
    data["photo_status"] = "assessed"
    if not data["detections"]:
        data["detections"] = [{"class": CLASSES[0], "confidence": 0.9}]
    det = data["detections"][idx % len(data["detections"])]
    det["box"] = geometry
    with pytest.raises(TriageInputError) as info:
        evaluate(RULES, data, PARAMS[profile])
    assert info.value.code == "DETECTION_INVALID"


@SETTINGS
@given(inputs(), st.integers(0, 10))
def test_pain_monotone_and_unknown_not_below_zero(
    case: tuple[str, dict[str, Any]], score: int
) -> None:
    profile, data = case
    scores = [level(profile, {**data, "pain": s}) for s in range(score + 1)]
    assert scores == sorted(scores)
    assert level(profile, {**data, "pain": "unknown"}) >= level(profile, {**data, "pain": 0})


@SETTINGS
@given(inputs(), st.integers(0, 300))
def test_unknown_age_never_lower_than_known_age(case: tuple[str, dict[str, Any]], age: int) -> None:
    profile, data = case
    assert level(profile, {**data, "age_months": None}) >= level(
        profile, {**data, "age_months": age}
    )


@SETTINGS
@given(inputs())
def test_missing_photo_never_lower_than_empty_photo(case: tuple[str, dict[str, Any]]) -> None:
    profile, data = case
    empty = {**data, "photo_status": "assessed", "detections": []}
    missing = {**data, "photo_status": "not_taken", "detections": []}
    assert level(profile, missing) >= level(profile, empty)


@SETTINGS
@given(case=inputs())
def test_output_invariants(case: tuple[str, dict[str, Any]]) -> None:
    profile, data = case
    frozen = copy.deepcopy(data)
    out = evaluate(RULES, data, PARAMS[profile])
    assert data == frozen  # pure: input untouched
    assert out == evaluate(RULES, data, PARAMS[profile])  # deterministic
    assert set(out) == {
        "level",
        "reasons",
        "lesions",
        "advice_ids",
        "referral_template_id",
        "rules_version",
    }
    assert out["reasons"]
    for advice_id in RULES["always_advice"]:
        assert advice_id in out["advice_ids"]
    assert out["referral_template_id"]
    if out["level"] == "routine":
        assert "ADV_RESCREEN_3_6_MONTHS" in out["advice_ids"]
    for reason in out["reasons"]:
        assert find_forbidden(reason["text"], FORBIDDEN) == []


@SETTINGS
@given(inputs(), st.sampled_from(QUESTIONS), st.sampled_from([None, "", "YES", True, 1, "no "]))
def test_missing_or_invalid_answer_is_an_error_never_a_result(
    case: tuple[str, dict[str, Any]], q: str, bad: object
) -> None:
    profile, data = case
    missing = copy.deepcopy(data)
    del missing["symptoms"][q]
    with pytest.raises(TriageInputError):
        evaluate(RULES, missing, PARAMS[profile])
    invalid = copy.deepcopy(data)
    invalid["symptoms"][q] = bad
    with pytest.raises(TriageInputError):
        evaluate(RULES, invalid, PARAMS[profile])
