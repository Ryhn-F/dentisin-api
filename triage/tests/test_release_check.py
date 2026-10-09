"""The release gate must block today and pass only for a fully dentist-approved rule set."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from triage.export import main as export_main
from triage.forbidden import ForbiddenList
from triage.release_check import collect_blockers, main

TEST_VALUES = {
    "small_lesion_rel_area": 0.02,
    "many_lesions_n": 3,
    "cavitated_min_confidence": 0.5,
    "noncavitated_min_confidence": 0.3,
    "pain_scheduled_min_score": 2,
    "pain_urgent_min_score": 6,
    "night_pain_level": "urgent",
    "unknown_red_flag_level": "emergency",
    "white_spot_level": "scheduled",
    "pain_unknown_level": "scheduled",
    "validated_age_months_min": 72,
    "validated_age_months_max": 155,
    "age_out_of_range_level": "scheduled",
    "photo_not_assessed_level": "scheduled",
    "lesion_area_reference": "image",
}  # synthetic, only to exercise the "pass" path of the gate


def approved(rules: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(rules)
    out["status"] = "APPROVED"
    review = {"reviewed_by": "SYNTHETIC_TEST_REVIEWER", "reviewed_at": "2000-01-01"}
    for name, spec in out["params"].items():
        spec.update(value=TEST_VALUES[name], status="SET", **review)
    for item in [*out["rules"], *out["params"].values()]:
        item.update(review, source=item["source"].replace("[PERLU VERIFIKASI]", "").strip())
    out["texts_review"].update(review)
    for contact in out["contacts"].values():
        contact["status"] = "VERIFIED"
    return out


def graded_cases(n: int) -> dict[str, Any]:
    return {"cases": [{"graded_by_drg": True}] * n}


def test_current_rules_are_blocked(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 1
    out = capsys.readouterr().out
    assert "RELEASE BLOCKED" in out
    assert "PLACEHOLDER" in out
    assert "not reviewed by a dentist" in out
    assert "PERLU VERIFIKASI" in out
    assert "graded_by_drg" in out


def test_blockers_list_every_category(rules: dict[str, Any], forbidden: ForbiddenList) -> None:
    planted = copy.deepcopy(approved(rules))
    planted["advice"][0]["text"] = "Pasti sembuh."
    blockers = collect_blockers(planted, graded_cases(29), ["rules.json"], forbidden)
    assert blockers == [
        "only 29 cases graded_by_drg; need >= 30",
        "dist/rules.json is stale",
        "forbidden phrase at $.advice[0].text: 'pasti'",
    ]
    assert collect_blockers(approved(rules), graded_cases(30), [], forbidden) == []
    assert len(collect_blockers(rules, graded_cases(0), [], forbidden)) > 40


def test_approved_rules_pass_end_to_end(
    tmp_path: Path, rules: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(yaml.safe_dump(approved(rules), allow_unicode=True), encoding="utf-8")
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps(graded_cases(30)), encoding="utf-8")
    dist = tmp_path / "dist"
    assert export_main(["--rules", str(rules_path), "--dist", str(dist)]) == 0
    args = ["--rules", str(rules_path), "--cases", str(cases_path), "--dist", str(dist)]
    assert main(args) == 0
    assert "Release check passed." in capsys.readouterr().out
