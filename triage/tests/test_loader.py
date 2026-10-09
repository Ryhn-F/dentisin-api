"""Rules validation: schema errors and every semantic invariant."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from triage import RulesValidationError
from triage.loader import load_rules, semantic_errors, validate_rules
from triage.paths import DIST_DIR

Mutator = Callable[[dict[str, Any]], None]


def _rule(rules: dict[str, Any], rule_id: str) -> dict[str, Any]:
    found: dict[str, Any] = next(r for r in rules["rules"] if r["id"] == rule_id)
    return found


def _set_param(name: str, **fields: Any) -> Mutator:
    def mutate(rules: dict[str, Any]) -> None:
        rules["params"][name].update(fields)

    return mutate


def _dup_rule(rules: dict[str, Any]) -> None:
    rules["rules"].insert(0, copy.deepcopy(rules["rules"][0]))


def _no_assessed_status(rules: dict[str, Any]) -> None:
    for status in rules["photo_statuses"]:
        status["assessed"] = False


def _unknown_advice(rules: dict[str, Any]) -> None:
    rules["levels"][0]["advice"] = ["ADV_DOES_NOT_EXIST"]


def _unknown_contact(rules: dict[str, Any]) -> None:
    rules["advice"][0]["uses_contact"] = "nope"


def _class_param_wrong_type(rules: dict[str, Any]) -> None:
    rules["detection_classes"][0]["min_confidence_param"] = "night_pain_level"


def _class_param_missing(rules: dict[str, Any]) -> None:
    rules["detection_classes"][0]["min_confidence_param"] = "does_not_exist"


def _area_reference_allowed(rules: dict[str, Any]) -> None:
    rules["params"]["lesion_area_reference"]["allowed"] = ["image"]


def _area_reference_param_missing(rules: dict[str, Any]) -> None:
    rules["lesion_size"]["area_reference_param"] = "does_not_exist"


def _default_not_last(rules: dict[str, Any]) -> None:
    rules["rules"].insert(0, rules["rules"].pop())


def _default_wrong_level(rules: dict[str, Any]) -> None:
    rules["rules"][-1]["level"] = "scheduled"


def _two_defaults(rules: dict[str, Any]) -> None:
    rules["rules"].append(copy.deepcopy(rules["rules"][-1]) | {"id": "DEFAULT_TWO"})


def _rule_unknown_level(rules: dict[str, Any]) -> None:
    _rule(rules, "DET_CAVITY_LARGE")["level"] = "whenever"


def _rule_level_param_wrong(rules: dict[str, Any]) -> None:
    _rule(rules, "SYM_NIGHT_PAIN")["level_param"] = "many_lesions_n"


def _rule_unknown_symptom(rules: dict[str, Any]) -> None:
    _rule(rules, "SYM_NIGHT_PAIN")["when"]["symptom"] = "headache"


def _rule_unknown_class(rules: dict[str, Any]) -> None:
    _rule(rules, "DET_CAVITY_LARGE")["when"]["class"] = "tooth"


def _rule_count_param_wrong(rules: dict[str, Any]) -> None:
    _rule(rules, "DET_CAVITY_MANY")["when"]["count_param"] = "small_lesion_rel_area"


def _red_flag_downgraded(rules: dict[str, Any]) -> None:
    _rule(rules, "RF_FACIAL_SWELLING")["level"] = "urgent"


def _red_flag_rule_removed(rules: dict[str, Any]) -> None:
    rules["rules"] = [r for r in rules["rules"] if r["id"] != "RF_DENTAL_TRAUMA"]


SEMANTIC_CASES: list[tuple[str, Mutator, str]] = [
    ("duplicate rule", _dup_rule, "duplicate rule id"),
    ("no assessed status", _no_assessed_status, "at least one photo status"),
    ("unknown advice", _unknown_advice, "unknown advice id"),
    ("unknown contact", _unknown_contact, "unknown contact"),
    (
        "number value out of range",
        _set_param("small_lesion_rel_area", value=2),
        "outside [min, max]",
    ),
    ("level value not allowed", _set_param("night_pain_level", value="routine"), "not in allowed"),
    (
        "level param unknown level",
        _set_param("night_pain_level", allowed=["emergency", "later"]),
        "allows unknown level",
    ),
    (
        "level fallback not highest",
        _set_param("night_pain_level", if_unset="urgent"),
        "highest allowed level",
    ),
    ("class param wrong type", _class_param_wrong_type, "must be of type"),
    ("class param missing", _class_param_missing, "unknown param"),
    ("area reference allowed", _area_reference_allowed, "exactly 'image' and 'frame'"),
    ("area reference missing", _area_reference_param_missing, "unknown param"),
    ("default not last", _default_not_last, "exactly one default rule"),
    ("default wrong level", _default_wrong_level, "exactly one default rule"),
    ("two defaults", _two_defaults, "exactly one default rule"),
    ("rule unknown level", _rule_unknown_level, "unknown level"),
    ("rule level_param wrong type", _rule_level_param_wrong, "must be of type"),
    ("rule unknown symptom", _rule_unknown_symptom, "unknown symptom"),
    ("rule unknown class", _rule_unknown_class, "unknown class"),
    ("rule count param wrong type", _rule_count_param_wrong, "must be of type"),
    ("red flag downgraded", _red_flag_downgraded, "has no fixed 'emergency' rule"),
    ("red flag rule removed", _red_flag_rule_removed, "has no fixed 'emergency' rule"),
]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [(m, msg) for _, m, msg in SEMANTIC_CASES],
    ids=[name for name, _, _ in SEMANTIC_CASES],
)
def test_semantic_invariants(rules: dict[str, Any], mutate: Mutator, message: str) -> None:
    broken = copy.deepcopy(rules)
    mutate(broken)
    errors = semantic_errors(broken)
    assert any(message in e for e in errors), errors
    with pytest.raises(RulesValidationError) as info:
        validate_rules(broken)
    assert info.value.code in ("RULES_SEMANTIC", "RULES_SCHEMA")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.pop("rules"),
        lambda r: r["questions"][0].update(group="maybe"),
        lambda r: r.update(answers=[True, False, "unknown"]),  # YAML 1.1 yes/no pitfall
        lambda r: _rule(r, "RF_LETHARGY").pop("rationale"),
        lambda r: _rule(r, "RF_LETHARGY").update(level_param="night_pain_level"),
        lambda r: _rule(r, "DET_NONCAV_ATTENTION").update(level="urgent"),
        lambda r: r["params"]["many_lesions_n"].update(value=3),  # PLACEHOLDER must be null
        lambda r: r["params"]["many_lesions_n"].update(allowed=["a"]),
        lambda r: r["params"]["night_pain_level"].update(min=0),
    ],
)
def test_schema_rejects(rules: dict[str, Any], mutate: Callable[[dict[str, Any]], Any]) -> None:
    broken = copy.deepcopy(rules)
    mutate(broken)
    with pytest.raises(RulesValidationError) as info:
        validate_rules(broken)
    assert info.value.code == "RULES_SCHEMA"


def test_semantic_duplicate_checks_ignore_unique_ids(rules: dict[str, Any]) -> None:
    broken = copy.deepcopy(rules)
    broken["levels"][1]["rank"] = broken["levels"][0]["rank"]
    assert any("duplicate level rank" in e for e in semantic_errors(broken))


def test_semantic_area_reference_without_allowed(rules: dict[str, Any]) -> None:
    broken = copy.deepcopy(rules)
    del broken["params"]["lesion_area_reference"]["allowed"]
    broken["params"]["lesion_area_reference"]["type"] = "number"
    broken["params"]["lesion_area_reference"].update(min=0, max=1, value=None, if_unset=0)
    errors = semantic_errors(broken)
    assert any("exactly 'image' and 'frame'" in e for e in errors)


def test_load_rules_reads_json_export(rules: dict[str, Any]) -> None:
    assert load_rules(DIST_DIR / "rules.json") == rules


def test_load_rules_rejects_invalid_file(tmp_path: Path, rules: dict[str, Any]) -> None:
    path = tmp_path / "rules.json"
    path.write_text(json.dumps({**rules, "status": "MAYBE"}), encoding="utf-8")
    with pytest.raises(RulesValidationError):
        load_rules(path)
