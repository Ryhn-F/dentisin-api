"""Load and validate ``rules.yaml`` (schema + semantic invariants).

The TypeScript app does not need to port this module: CI validates the rules
and exports ``dist/rules.json``; the app verifies the checksum and trusts it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from triage.engine import ANY_CLASS
from triage.errors import RulesValidationError
from triage.paths import RULES_PATH, SCHEMA_PATH

PENDING_REVIEW = (None, "PENDING_DRG")


def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def read_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_schema(path: Path = SCHEMA_PATH) -> dict[str, Any]:
    schema: dict[str, Any] = read_json(path)
    Draft202012Validator.check_schema(schema)
    return schema


def schema_errors(rules: object, schema: Mapping[str, Any] | None = None) -> list[str]:
    validator = Draft202012Validator(schema if schema is not None else load_schema())
    return sorted(
        f"{'/'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
        for err in validator.iter_errors(rules)
    )


def _duplicates(ids: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    dupes: list[str] = []
    for item in ids:
        if item in seen:
            dupes.append(item)
        seen.add(item)
    return dupes


CONDITION_PARAM_KEYS = ("param", "min_param", "below_param", "count_param", "max_param")


def _param_refs(when: Mapping[str, Any]) -> list[str]:
    """Param names referenced by a condition (all condition params are integers)."""
    return [when[key] for key in CONDITION_PARAM_KEYS if key in when]


def semantic_errors(rules: Mapping[str, Any]) -> list[str]:
    """Invariants that JSON Schema cannot express. Empty list = valid."""
    errors: list[str] = []
    levels = rules["levels"]
    ranks = {level["id"]: level["rank"] for level in levels}
    params: Mapping[str, Any] = rules["params"]
    advice_ids = {item["id"] for item in rules["advice"]}
    questions = {q["id"]: q for q in rules["questions"]}
    class_ids = {c["id"] for c in rules["detection_classes"]}
    top_level = max(ranks, key=lambda level_id: ranks[level_id])

    for label, ids in (
        ("level", [level["id"] for level in levels]),
        ("level rank", [str(level["rank"]) for level in levels]),
        ("question", [q["id"] for q in rules["questions"]]),
        ("advice", [a["id"] for a in rules["advice"]]),
        ("rule", [r["id"] for r in rules["rules"]]),
        ("detection class", [c["id"] for c in rules["detection_classes"]]),
        ("photo status", [s["id"] for s in rules["photo_statuses"]]),
    ):
        errors.extend(f"duplicate {label} id '{dupe}'" for dupe in _duplicates(ids))

    if not any(s["assessed"] for s in rules["photo_statuses"]):
        errors.append("at least one photo status must be 'assessed: true'")

    referenced_advice = list(rules["always_advice"]) + list(rules["photo_assessed_advice"])
    for level in levels:
        referenced_advice.extend(level["advice"])
    for rule in rules["rules"]:
        referenced_advice.extend(rule["advice"])
    errors.extend(
        f"unknown advice id '{a}'" for a in dict.fromkeys(referenced_advice) if a not in advice_ids
    )
    errors.extend(
        f"advice '{a['id']}' uses unknown contact '{a['uses_contact']}'"
        for a in rules["advice"]
        if "uses_contact" in a and a["uses_contact"] not in rules["contacts"]
    )

    def expect_param(name: str, kinds: tuple[str, ...], where: str) -> None:
        if name not in params:
            errors.append(f"{where}: unknown param '{name}'")
        elif params[name]["type"] not in kinds:
            errors.append(f"{where}: param '{name}' must be of type {kinds}")

    for name, spec in params.items():
        if spec["type"] in ("number", "integer"):
            for key in ("value", "if_unset"):
                value = spec[key]
                if value is not None and not spec["min"] <= value <= spec["max"]:
                    errors.append(f"param '{name}'.{key} outside [min, max]")
        else:
            for key in ("value", "if_unset"):
                if spec[key] is not None and spec[key] not in spec["allowed"]:
                    errors.append(f"param '{name}'.{key} not in allowed")
        if spec["type"] == "level":
            unknown = [a for a in spec["allowed"] if a not in ranks]
            errors.extend(f"param '{name}' allows unknown level '{a}'" for a in unknown)
            if not unknown and spec["if_unset"] != max(
                spec["allowed"], key=lambda level_id: ranks[level_id]
            ):
                errors.append(f"param '{name}'.if_unset must be the highest allowed level")

    for cls in rules["detection_classes"]:
        expect_param(cls["min_confidence_param"], ("number",), f"class '{cls['id']}'")
    if ANY_CLASS in class_ids:
        errors.append(f"detection class id '{ANY_CLASS}' is reserved")

    default_rules = [r for r in rules["rules"] if r["when"]["type"] == "no_higher_level_rule_fired"]
    lowest = min(ranks, key=lambda level_id: ranks[level_id])
    if (
        len(default_rules) != 1
        or rules["rules"][-1] is not default_rules[0]
        or default_rules[0].get("level") != lowest
    ):
        errors.append("exactly one default rule (lowest level, fixed) must be the last rule")

    for rule in rules["rules"]:
        where = f"rule '{rule['id']}'"
        when = rule["when"]
        if "level" in rule and rule["level"] not in ranks:
            errors.append(f"{where}: unknown level '{rule['level']}'")
        if "level_param" in rule:
            expect_param(rule["level_param"], ("level",), where)
        if when["type"] == "symptom_answer" and when["symptom"] not in questions:
            errors.append(f"{where}: unknown symptom '{when['symptom']}'")
        if when["type"] == "detection_count_at_least" and when["class"] not in (
            class_ids | {ANY_CLASS}
        ):
            errors.append(f"{where}: unknown class '{when['class']}'")
        for name in _param_refs(when):
            expect_param(name, ("integer",), where)

    # Safety invariant: every red-flag question has a fixed top-level rule on "yes".
    for qid, question in questions.items():
        if question["group"] != "red_flag":
            continue
        if not any(
            r["when"]["type"] == "symptom_answer"
            and r["when"]["symptom"] == qid
            and "yes" in r["when"]["answers"]
            and r.get("level") == top_level
            for r in rules["rules"]
        ):
            errors.append(f"red flag '{qid}' has no fixed '{top_level}' rule on 'yes'")
    return errors


def validate_rules(rules: object, schema: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Raise ``RulesValidationError`` (schema first, then semantics) or return the rules."""
    problems = schema_errors(rules, schema)
    if problems:
        raise RulesValidationError("RULES_SCHEMA", "; ".join(problems))
    assert isinstance(rules, dict)  # guaranteed by the schema (type: object)
    problems = semantic_errors(rules)
    if problems:
        raise RulesValidationError("RULES_SEMANTIC", "; ".join(problems))
    return rules


def load_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    """Read ``rules.yaml`` (or a ``.json`` export) and validate it."""
    data = read_json(path) if path.suffix == ".json" else read_yaml(path)
    return validate_rules(data)
