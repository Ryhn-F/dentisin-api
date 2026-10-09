"""Checks on rules.yaml itself: schema, honesty markers, wording lint, dist freshness."""

from __future__ import annotations

import re
from typing import Any

from triage import evaluate, resolve_params
from triage.export import build_artifacts, stale_files
from triage.forbidden import ForbiddenList, find_forbidden, lint_document
from triage.loader import load_schema, schema_errors, semantic_errors
from triage.paths import DIST_DIR, PROJECT_DIR


def test_rules_match_schema_and_invariants(rules: dict[str, Any]) -> None:
    assert schema_errors(rules, load_schema()) == []
    assert semantic_errors(rules) == []


def test_nobody_has_reviewed_anything_yet(rules: dict[str, Any]) -> None:
    """No invented reviewers or dates: everything is PENDING_DRG until a dentist signs."""
    reviewed = [*rules["rules"], *rules["params"].values(), rules["texts_review"]]
    assert all(item["reviewed_by"] == "PENDING_DRG" for item in reviewed)
    assert all(item["reviewed_at"] is None for item in reviewed)
    assert rules["status"] == "DRAFT"


def test_clinical_params_are_placeholders(rules: dict[str, Any]) -> None:
    for name, spec in rules["params"].items():
        if spec["type"] == "enum":  # technical choice (area reference), still unreviewed
            assert spec["status"] == "PROPOSED", name
            continue
        assert spec["value"] is None, name
        assert spec["status"] == "PLACEHOLDER", name
    assert rules["params"]["small_lesion_rel_area"]["symbol"] == "X"
    assert rules["params"]["many_lesions_n"]["symbol"] == "N"


def test_engine_runs_on_placeholder_rules_with_fail_safe_fallbacks(rules: dict[str, Any]) -> None:
    params = resolve_params(rules)
    assert params["night_pain_level"] == "emergency"
    symptoms = {q["id"]: "no" for q in rules["questions"]}
    out = evaluate(
        rules,
        {
            "detections": [],
            "photo_status": "assessed",
            "reference_frame": None,
            "symptoms": symptoms,
            "age_months": 96,
            "pain": 0,
        },
        params,
    )
    # Age range is empty while unset, so nothing is reported as plain "routine".
    assert out["level"] == "scheduled"


def test_no_forbidden_phrases_in_any_rules_string(
    rules: dict[str, Any], forbidden: ForbiddenList
) -> None:
    assert len(forbidden.phrases) >= 10
    for phrase in (
        "diagnosis",
        "pasti",
        "Anda menderita",
        "positif karies",
        "aman",
        "tidak perlu ke dokter",
        "bebas karies",
    ):
        assert phrase in forbidden.phrases
    assert lint_document(rules, forbidden) == []


def test_lint_would_catch_a_planted_phrase(rules: dict[str, Any], forbidden: ForbiddenList) -> None:
    planted = {**rules, "rules": [{**rules["rules"][0], "message": "Anak Anda menderita karies."}]}
    assert lint_document(planted, forbidden) == [
        "$.rules[0].message: 'Anda menderita'",
        "$.rules[0].message: 'anak Anda menderita'",
    ]


def test_reason_messages_use_screening_language(rules: dict[str, Any]) -> None:
    for rule in rules["rules"]:
        text = rule["message"].lower()
        assert "indikasi" in text or "perlu diperiksa" in text, rule["id"]


def test_mandatory_texts(rules: dict[str, Any], forbidden: ForbiddenList) -> None:
    advice = {a["id"]: a["text"] for a in rules["advice"]}
    assert advice["ADV_DISCLAIMER_NOT_DIAGNOSIS"] in forbidden.mandatory
    assert find_forbidden(advice["ADV_DISCLAIMER_NOT_DIAGNOSIS"], forbidden) == []
    assert rules["always_advice"] == ["ADV_DISCLAIMER_NOT_DIAGNOSIS", "ADV_REFERRAL_CTA"]
    routine = next(lvl for lvl in rules["levels"] if lvl["id"] == "routine")
    assert any(re.search(r"3.6 bulan", advice[a]) for a in routine["advice"])
    assert all(lvl["referral_template_id"] for lvl in rules["levels"])


def test_level_param_fallbacks_are_the_more_cautious_choice(rules: dict[str, Any]) -> None:
    ranks = {lvl["id"]: lvl["rank"] for lvl in rules["levels"]}
    for spec in rules["params"].values():
        if spec["type"] == "level":
            assert ranks[spec["if_unset"]] == max(ranks[a] for a in spec["allowed"])


def test_dist_is_up_to_date(rules: dict[str, Any]) -> None:
    assert stale_files(DIST_DIR, build_artifacts(rules)) == []


def test_readme_documents_every_rule_and_param(rules: dict[str, Any]) -> None:
    readme = (PROJECT_DIR / "README.md").read_text(encoding="utf-8")
    for rule in rules["rules"]:
        assert rule["id"] in readme, rule["id"]
    for name in rules["params"]:
        assert name in readme, name
    assert "NOT met" in readme
