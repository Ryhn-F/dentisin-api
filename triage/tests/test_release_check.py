"""The release gate must block today and pass only for a fully reviewed rule set:
dentist-approved (``review_basis: dentist`` or absent) or literature-based with a team
reviewer and cited sources/cases (``review_basis: literature``, ADR 015)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator

from triage.export import main as export_main
from triage.forbidden import ForbiddenList
from triage.loader import read_json, schema_errors
from triage.paths import CASES_SCHEMA_PATH
from triage.release_check import collect_blockers, main, review_basis, reviewer_ok

TEST_VALUES = {
    "many_lesions_n": 3,
    "permanent_min_confidence": 0.5,
    "primary_min_confidence": 0.4,
    "caries_indication_level": "scheduled",
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


TEAM_REVIEWER = "SYNTHETIC_TEAM_MEMBER"  # tests only, not a real person


def literature(rules: dict[str, Any]) -> dict[str, Any]:
    """``approved(rules)`` on the literature basis (ADR 015), signed by a team member."""
    out = approved(rules)
    out["review_basis"] = "literature"
    for item in [*out["rules"], *out["params"].values(), out["texts_review"]]:
        item["reviewed_by"] = TEAM_REVIEWER
    return out


def cited_cases(n: int, *, graded: bool = False) -> dict[str, Any]:
    return {"cases": [{"graded_by_drg": graded, "expected_source": "SYNTHETIC_CITATION"}] * n}


def test_current_rules_are_blocked(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 1
    out = capsys.readouterr().out
    assert "review_basis: dentist" in out  # the field is absent from rules.yaml
    assert "RELEASE BLOCKED" in out
    assert "PLACEHOLDER" in out
    assert "not reviewed by a dentist" in out
    assert "PERLU VERIFIKASI" in out
    assert "graded_by_drg" in out


def test_shipped_rules_have_no_review_basis_yet(rules: dict[str, Any]) -> None:
    """Nothing is reviewed yet, on any basis: the field stays absent (= dentist) until 3.3."""
    assert "review_basis" not in rules
    assert review_basis(rules) == "dentist"


def test_absent_basis_equals_explicit_dentist(
    rules: dict[str, Any], forbidden: ForbiddenList
) -> None:
    dentist = copy.deepcopy(rules)
    dentist["review_basis"] = "dentist"
    for cases in (graded_cases(0), graded_cases(30), cited_cases(30)):
        assert collect_blockers(dentist, cases, [], forbidden) == collect_blockers(
            rules, cases, [], forbidden
        )


def test_dentist_basis_ignores_expected_source(
    rules: dict[str, Any], forbidden: ForbiddenList
) -> None:
    for ok in (approved(rules), {**approved(rules), "review_basis": "dentist"}):
        assert collect_blockers(ok, cited_cases(30), [], forbidden) == [
            "only 0 cases graded_by_drg; need >= 30"
        ]
        assert collect_blockers(ok, cited_cases(30, graded=True), [], forbidden) == []


def test_dentist_basis_still_rejects_pending_reviewers(
    rules: dict[str, Any], forbidden: ForbiddenList
) -> None:
    bad = approved(rules)
    bad["rules"][0]["reviewed_by"] = "PENDING_DRG"
    bad["params"]["many_lesions_n"]["reviewed_by"] = None
    assert collect_blockers(bad, graded_cases(30), [], forbidden) == [
        "param 'many_lesions_n': not reviewed by a dentist (reviewed_by)",
        f"rule '{bad['rules'][0]['id']}': not reviewed by a dentist (reviewed_by)",
    ]


def test_literature_basis_passes_with_team_reviewer_and_cited_cases(
    rules: dict[str, Any], forbidden: ForbiddenList
) -> None:
    ok = literature(rules)
    assert collect_blockers(ok, cited_cases(30), [], forbidden) == []
    # graded_by_drg is not what counts on the literature basis
    mixed = {"cases": graded_cases(30)["cases"] + cited_cases(29)["cases"]}
    assert collect_blockers(ok, mixed, [], forbidden) == [
        "only 29 cases carry an expected_source; need >= 30"
    ]


@pytest.mark.parametrize("reviewer", [None, "", "   ", "PENDING_DRG", "PLACEHOLDER", "null", "TBD"])
def test_literature_basis_rejects_placeholder_reviewers(
    rules: dict[str, Any], forbidden: ForbiddenList, reviewer: str | None
) -> None:
    bad = literature(rules)
    bad["params"]["many_lesions_n"]["reviewed_by"] = reviewer
    bad["texts_review"]["reviewed_by"] = reviewer
    assert collect_blockers(bad, cited_cases(30), [], forbidden) == [
        "param 'many_lesions_n': reviewed_by is not a named team reviewer (literature basis)",
        "texts 'texts_review': reviewed_by is not a named team reviewer (literature basis)",
    ]


def test_literature_basis_requires_a_filled_source(
    rules: dict[str, Any], forbidden: ForbiddenList
) -> None:
    bad = literature(rules)
    bad["params"]["many_lesions_n"]["source"] = "   "
    bad["rules"][0]["reviewed_at"] = None
    rule_id = bad["rules"][0]["id"]
    assert collect_blockers(bad, cited_cases(30), [], forbidden) == [
        "param 'many_lesions_n': source missing (literature basis needs a citation)",
        f"rule '{rule_id}': reviewed_at missing",
    ]
    # the same blank source is not a blocker on the dentist basis (unchanged behaviour)
    dentist = {**bad, "review_basis": "dentist"}
    assert collect_blockers(dentist, graded_cases(30), [], forbidden) == [
        f"rule '{rule_id}': reviewed_at missing"
    ]


def test_literature_basis_rejects_perlu_verifikasi(
    rules: dict[str, Any], forbidden: ForbiddenList
) -> None:
    bad = literature(rules)
    bad["rules"][0]["source"] += " [PERLU VERIFIKASI]"
    assert collect_blockers(bad, cited_cases(30), [], forbidden) == [
        f"rule '{bad['rules'][0]['id']}': source still marked PERLU VERIFIKASI"
    ]


def test_literature_basis_keeps_every_other_check(
    rules: dict[str, Any], forbidden: ForbiddenList
) -> None:
    """Only the reviewer/case requirements change: the current draft is still fully blocked."""
    draft = {**copy.deepcopy(rules), "review_basis": "literature"}
    blockers = collect_blockers(draft, cited_cases(0), ["rules.json"], forbidden)
    text = "\n".join(blockers)
    for expected in (
        "rules status is 'DRAFT', not 'APPROVED'",
        "status=PLACEHOLDER",
        "reviewed_by is not a named team reviewer",
        "source still marked PERLU VERIFIKASI",
        "contact 'emergency_numbers': status PERLU_VERIFIKASI",
        "only 0 cases carry an expected_source; need >= 30",
        "dist/rules.json is stale",
    ):
        assert expected in text


@pytest.mark.parametrize(
    ("case", "counted"),
    [
        ({"graded_by_drg": False}, False),
        ({"graded_by_drg": True}, False),
        ({"graded_by_drg": False, "expected_source": ""}, False),
        ({"graded_by_drg": False, "expected_source": "  "}, False),
        ({"graded_by_drg": False, "expected_source": "Pedoman X [PERLU VERIFIKASI]"}, False),
        ({"graded_by_drg": False, "expected_source": "SYNTHETIC_CITATION"}, True),
    ],
)
def test_expected_source_counting(
    rules: dict[str, Any], forbidden: ForbiddenList, case: dict[str, Any], counted: bool
) -> None:
    cases = {"cases": [case] * 30}
    blockers = collect_blockers(literature(rules), cases, [], forbidden)
    assert blockers == ([] if counted else ["only 0 cases carry an expected_source; need >= 30"])


def test_reviewer_ok_per_basis() -> None:
    assert reviewer_ok("Tim A", "literature")
    assert reviewer_ok("Tim A", "dentist")
    assert not reviewer_ok("pending_drg", "literature")
    assert not reviewer_ok(None, "dentist")
    assert not reviewer_ok("PENDING_DRG", "dentist")
    # dentist basis is unchanged: only null/PENDING_DRG are rejected there
    assert reviewer_ok("PLACEHOLDER", "dentist")


def test_literature_rules_pass_end_to_end(
    tmp_path: Path, rules: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(yaml.safe_dump(literature(rules), allow_unicode=True), encoding="utf-8")
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps(cited_cases(30)), encoding="utf-8")
    dist = tmp_path / "dist"
    assert export_main(["--rules", str(rules_path), "--dist", str(dist)]) == 0
    args = ["--rules", str(rules_path), "--cases", str(cases_path), "--dist", str(dist)]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "review_basis: literature" in out
    assert "Release check passed." in out


def test_schemas_accept_the_new_optional_fields(
    rules: dict[str, Any], cases_doc: dict[str, Any]
) -> None:
    assert schema_errors({**rules, "review_basis": "literature"}) == []
    assert schema_errors({**rules, "review_basis": "dentist"}) == []
    assert schema_errors({**rules, "review_basis": "team"})
    cases_schema = Draft202012Validator(read_json(CASES_SCHEMA_PATH))
    cited = copy.deepcopy(cases_doc)
    cited["cases"][0]["expected_source"] = "SYNTHETIC_CITATION"
    assert cases_schema.is_valid(cases_doc)
    assert cases_schema.is_valid(cited)
    cited["cases"][0]["expected_source"] = ""
    assert not cases_schema.is_valid(cited)


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
