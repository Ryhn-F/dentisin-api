"""Runs the shared, language-neutral table tests/cases.json (the Jest suite runs the same file)."""

from __future__ import annotations

from typing import Any

import pytest
from jsonschema import Draft202012Validator

from conftest import engine_input
from triage import TriageInputError, evaluate, resolve_params
from triage.export import build_input_schema, build_output_schema
from triage.loader import read_json
from triage.paths import CASES_PATH, CASES_SCHEMA_PATH

CASES: list[dict[str, Any]] = read_json(CASES_PATH)["cases"]
OK_CASES = [c for c in CASES if "error" not in c["expected"]]
ERROR_CASES = [c for c in CASES if "error" in c["expected"]]
# Error codes that may come from constraints JSON Schema cannot express (x_min < x_max,
# detections need an assessed photo, frame required for frame-relative sizing).
CROSS_FIELD_CODES = {
    "DETECTIONS_WITHOUT_PHOTO",
    "REFERENCE_FRAME_REQUIRED",
    "BOX_INVALID",
    "REFERENCE_FRAME_INVALID",
}


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_shared_case(case: dict[str, Any], rules: dict[str, Any], profiles: dict[str, Any]) -> None:
    params = resolve_params(rules, profiles[case["input"]["params_profile"]]["values"])
    expected = case["expected"]
    if "error" in expected:
        with pytest.raises(TriageInputError) as info:
            evaluate(rules, engine_input(case["input"]), params)
        assert info.value.code == expected["error_code"]
        return

    out = evaluate(rules, engine_input(case["input"]), params)
    assert out["level"] == expected["level"]
    assert [r["id"] for r in out["reasons"]] == expected["reasons_ids"]
    assert out["advice_ids"] == expected["advice_ids"]
    assert out["referral_template_id"] == expected["referral_template_id"]
    if "lesions" in expected:
        got = [
            {key: lesion[key] for key in ("detection_index", "counted", "size")}
            for lesion in out["lesions"]
        ]
        assert got == expected["lesions"]
    Draft202012Validator(build_output_schema(rules)).validate(out)


def test_cases_file_matches_schema(cases_doc: dict[str, Any]) -> None:
    Draft202012Validator(read_json(CASES_SCHEMA_PATH)).validate(cases_doc)


def test_case_table_size_and_grading(cases_doc: dict[str, Any]) -> None:
    assert len(OK_CASES) >= 60
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids))
    # Honesty: nobody has graded anything yet; the >= 30 graded-cases bar is NOT met.
    assert all(case["graded_by_drg"] is False for case in CASES)
    assert "NOT met" in cases_doc["graded_by_drg_note"]


@pytest.mark.parametrize("case", OK_CASES, ids=[c["id"] for c in OK_CASES])
def test_valid_case_inputs_match_input_schema(case: dict[str, Any], rules: dict[str, Any]) -> None:
    Draft202012Validator(build_input_schema(rules)).validate(engine_input(case["input"]))


@pytest.mark.parametrize("case", ERROR_CASES, ids=[c["id"] for c in ERROR_CASES])
def test_error_case_inputs_rejected_by_schema_unless_cross_field(
    case: dict[str, Any], rules: dict[str, Any]
) -> None:
    valid = Draft202012Validator(build_input_schema(rules)).is_valid(engine_input(case["input"]))
    code = case["expected"]["error_code"]
    if valid:
        assert code in CROSS_FIELD_CODES
    else:
        assert code not in {"DETECTIONS_WITHOUT_PHOTO", "REFERENCE_FRAME_REQUIRED"}


def test_every_rule_is_triggered_by_some_case(rules: dict[str, Any]) -> None:
    triggered = {rid for c in OK_CASES for rid in c["expected"]["reasons_ids"]}
    assert {r["id"] for r in rules["rules"]} == triggered


def test_every_expected_id_exists(rules: dict[str, Any], profiles: dict[str, Any]) -> None:
    rule_ids = {r["id"] for r in rules["rules"]}
    advice_ids = {a["id"] for a in rules["advice"]}
    level_ids = {lvl["id"] for lvl in rules["levels"]}
    refs = {lvl["referral_template_id"] for lvl in rules["levels"]}
    for case in OK_CASES:
        exp = case["expected"]
        assert set(exp["reasons_ids"]) <= rule_ids, case["id"]
        assert set(exp["advice_ids"]) <= advice_ids, case["id"]
        assert exp["level"] in level_ids
        assert exp["referral_template_id"] in refs
    assert {c["input"]["params_profile"] for c in CASES} == set(profiles)


def test_every_advice_level_and_red_flag_is_covered(rules: dict[str, Any]) -> None:
    assert {a for c in OK_CASES for a in c["expected"]["advice_ids"]} == {
        a["id"] for a in rules["advice"]
    }
    assert {c["expected"]["level"] for c in OK_CASES} == {lvl["id"] for lvl in rules["levels"]}
    red_flags = [q["id"] for q in rules["questions"] if q["group"] == "red_flag"]
    for flag in red_flags:
        alone = [
            c
            for c in OK_CASES
            if [k for k, v in c["input"]["symptoms"].items() if v != "no"] == [flag]
            and c["input"]["symptoms"][flag] == "yes"
        ]
        assert alone, f"no case with red flag '{flag}' alone"
        assert all(c["expected"]["level"] == "emergency" for c in alone)
