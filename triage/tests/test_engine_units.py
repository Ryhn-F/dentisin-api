"""Unit tests for engine branches not expressible in the shared table (Python-only types)."""

from __future__ import annotations

import ast
import copy
from typing import Any

import pytest

from triage import TriageInputError, TriageParamsError, check_params, evaluate, resolve_params
from triage.engine import rule_level
from triage.paths import PACKAGE_DIR


@pytest.fixture
def params(rules: dict[str, Any], profiles: dict[str, Any]) -> dict[str, Any]:
    return resolve_params(rules, profiles["test"]["values"])


def base_input(rules: dict[str, Any]) -> dict[str, Any]:
    return {
        "detections": [],
        "photo_status": "assessed",
        "reference_frame": None,
        "symptoms": {q["id"]: "no" for q in rules["questions"]},
        "age_months": 96,
        "pain": 0,
    }


CAV: dict[str, Any] = {
    "class": "caries_cavitated",
    "confidence": 0.9,
    "box": {"x_min": 0.1, "y_min": 0.1, "x_max": 0.2, "y_max": 0.2},
}


@pytest.mark.parametrize(
    ("patch", "code"),
    [
        ({"age_months": True}, "AGE_INVALID"),
        ({"age_months": float("nan")}, "AGE_INVALID"),
        ({"pain": True}, "PAIN_INVALID"),
        ({"pain": 3.5}, "PAIN_INVALID"),
        ({"photo_status": ["assessed"]}, "PHOTO_STATUS_INVALID"),
        ({"symptoms": None}, "SYMPTOMS_INVALID"),
        ({"detections": "caries"}, "DETECTIONS_INVALID"),
        ({"detections": [["caries_cavitated", 0.9]]}, "DETECTION_INVALID"),
        ({"detections": [{**CAV, "extra": 1}]}, "DETECTION_INVALID"),
        ({"detections": [{**CAV, "class": 7}]}, "UNKNOWN_CLASS"),
        ({"detections": [{**CAV, "confidence": True}]}, "CONFIDENCE_INVALID"),
        ({"detections": [{**CAV, "confidence": -0.1}]}, "CONFIDENCE_INVALID"),
        ({"detections": [{**CAV, "box": [0.1, 0.1, 0.2, 0.2]}]}, "BOX_INVALID"),
        ({"detections": [{**CAV, "box": {**CAV["box"], "x_min": "0"}}]}, "BOX_INVALID"),
        ({"detections": [{**CAV, "box": {**CAV["box"], "y_max": 0.1}}]}, "BOX_INVALID"),
        ({"reference_frame": "full"}, "REFERENCE_FRAME_INVALID"),
    ],
)
def test_invalid_input_raises_typed_error(
    rules: dict[str, Any], params: dict[str, Any], patch: dict[str, Any], code: str
) -> None:
    with pytest.raises(TriageInputError) as info:
        evaluate(rules, {**base_input(rules), **patch}, params)
    assert info.value.code == code
    assert code in str(info.value)


def test_non_object_input(rules: dict[str, Any], params: dict[str, Any]) -> None:
    with pytest.raises(TriageInputError) as info:
        evaluate(rules, [base_input(rules)], params)
    assert info.value.code == "INPUT_NOT_OBJECT"


def test_integral_float_and_tuple_accepted_like_json(
    rules: dict[str, Any], params: dict[str, Any]
) -> None:
    """JS cannot tell 96 from 96.0; Python must accept both for parity."""
    out = evaluate(
        rules, {**base_input(rules), "age_months": 96.0, "pain": 2.0, "detections": (CAV,)}, params
    )
    assert out["level"] == "scheduled"


@pytest.mark.parametrize(
    ("patch", "code"),
    [
        ({"not_a_param": 1}, "PARAM_UNKNOWN"),
        ({"many_lesions_n": 2.5}, "PARAM_INVALID"),
        ({"many_lesions_n": 0}, "PARAM_INVALID"),
        ({"small_lesion_rel_area": "0.1"}, "PARAM_INVALID"),
        ({"small_lesion_rel_area": True}, "PARAM_INVALID"),
        ({"night_pain_level": "routine"}, "PARAM_INVALID"),
        ({"lesion_area_reference": 1}, "PARAM_INVALID"),
    ],
)
def test_invalid_params(
    rules: dict[str, Any], params: dict[str, Any], patch: dict[str, Any], code: str
) -> None:
    with pytest.raises(TriageParamsError) as info:
        check_params(rules, {**params, **patch})
    assert info.value.code == code
    with pytest.raises(TriageParamsError):
        evaluate(rules, base_input(rules), {**params, **patch})


def test_missing_param(rules: dict[str, Any], params: dict[str, Any]) -> None:
    incomplete = {k: v for k, v in params.items() if k != "many_lesions_n"}
    with pytest.raises(TriageParamsError) as info:
        check_params(rules, incomplete)
    assert info.value.code == "PARAM_MISSING"


def test_resolve_params_rejects_unknown_override(rules: dict[str, Any]) -> None:
    with pytest.raises(TriageParamsError) as info:
        resolve_params(rules, {"x": 1})
    assert info.value.code == "PARAM_UNKNOWN"


def test_resolve_params_precedence(rules: dict[str, Any]) -> None:
    patched = copy.deepcopy(rules)
    patched["params"]["many_lesions_n"].update(value=4, status="SET")
    assert resolve_params(patched)["many_lesions_n"] == 4  # rules.yaml value
    assert resolve_params(patched, {"many_lesions_n": 2})["many_lesions_n"] == 2  # override
    assert resolve_params(patched, {"many_lesions_n": None})["many_lesions_n"] == 1  # if_unset


def test_rule_level_kinds(rules: dict[str, Any], params: dict[str, Any]) -> None:
    by_id = {r["id"]: r for r in rules["rules"]}
    assert rule_level(by_id["DET_NONCAV_ATTENTION"], params) is None
    assert rule_level(by_id["SYM_NIGHT_PAIN"], params) == params["night_pain_level"]
    assert rule_level(by_id["RF_TRISMUS"], params) == "emergency"


def test_engine_does_not_mutate_input_and_is_deterministic(
    rules: dict[str, Any], params: dict[str, Any]
) -> None:
    data = {**base_input(rules), "detections": [CAV, CAV, CAV], "pain": "unknown"}
    frozen = copy.deepcopy(data)
    rules_before = copy.deepcopy(rules)
    first = evaluate(rules, data, params)
    assert evaluate(rules, data, params) == first
    assert data == frozen
    assert rules == rules_before


def test_engine_module_is_pure() -> None:
    """Only side-effect-free imports, so the TS port can be 1:1 (no I/O, clock, randomness)."""
    tree = ast.parse((PACKAGE_DIR / "engine.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert imported <= {"__future__", "math", "collections.abc", "typing", "triage.errors"}
    source = (PACKAGE_DIR / "engine.py").read_text(encoding="utf-8")
    for banned in ("open(", "print(", "time.", "random.", "datetime", "os.", "global "):
        assert banned not in source
