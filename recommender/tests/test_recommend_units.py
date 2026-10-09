"""Unit tests that exercise recommend() with injected rules (approved, clamped, broken)."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from recommender.recommend import (
    InvalidAgeError,
    InvalidInputError,
    RecommenderError,
    RulesError,
    load_products,
    load_rules,
    recommend,
)
from recommender.tests.helpers import approve_everything, verify_everything


def test_shipped_data_is_flagged_for_review() -> None:
    result = recommend(48)
    assert result["status"]["needs_review"] is True
    assert "ppm_decision_pending" in result["status"]["reasons"]
    assert "products_unverified" in result["status"]["reasons"]
    assert result["status"]["generic_message"]["id"] == "generic_conservative"
    assert result["rules_version"] == load_rules()["rules_version"]


def test_fully_approved_rules_and_verified_products_are_not_flagged() -> None:
    rules = approve_everything(load_rules())
    products = verify_everything(load_products())["products"]
    # Placeholders have unknown properties; make them concrete so they pass no filters needed.
    result = recommend(48, rules=rules, products=products)
    assert result["status"] == {"needs_review": False, "reasons": [], "generic_message": None}
    # The dentist's selected candidate is the only range returned.
    assert [(o["min"], o["max"]) for o in result["fluoride"]["ppm_options"]] == [(1000, 1450)]
    assert result["fluoride"]["decision_pending"] is False
    assert {p["ppm"] for p in result["products"]} <= set(range(1000, 1451))
    assert all(p["verified"] for p in result["products"])


def test_filters_review_is_reported_only_when_filters_are_used() -> None:
    rules = approve_everything(load_rules())
    rules["filters"]["status"] = "needs_review"
    products = verify_everything(load_products())["products"]
    assert recommend(48, rules=rules, products=products)["status"]["needs_review"] is False
    flagged = recommend(48, ["cinnamon"], rules=rules, products=products)
    assert "rules:filters:needs_review" in flagged["status"]["reasons"]
    flagged = recommend(48, prefs={"halal": True}, rules=rules, products=products)
    assert "rules:filters:needs_review" in flagged["status"]["reasons"]


def test_regulatory_ceiling_clamps_and_drops_candidates() -> None:
    rules = approve_everything(load_rules())
    # approve_everything selected the first candidate; unselect to see both.
    for band in rules["age_bands"]:
        band["fluoride_ppm"]["selected_candidate_id"] = None
    rules["regulatory_ceiling_ppm"]["value"] = 900
    result = recommend(48, rules=rules, products=[])
    options = result["fluoride"]["ppm_options"]
    assert [(o["candidate_id"], o["min"], o["max"]) for o in options] == [
        ("sni_legacy_500_1000", 500, 900)
    ]
    reasons = result["status"]["reasons"]
    assert "ppm_ceiling_applied" in reasons
    assert "ppm_candidate_above_ceiling:guideline_ge1000" in reasons


def test_band_without_candidates_returns_no_products() -> None:
    result = recommend(1, products=load_products()["products"])
    assert result["fluoride"]["ppm_options"] == []
    assert result["products"] == []
    assert result["notes"] == []


def test_school_band_has_no_supervision_text() -> None:
    result = recommend(90)
    assert result["fluoride"]["supervision"] == {
        "required": False,
        "until_age_months": 84,
        "text": None,
    }


def test_uncovered_age_raises_rules_error() -> None:
    rules = copy.deepcopy(load_rules())
    rules["age_bands"] = [b for b in rules["age_bands"] if b["id"] != "school_84_216m"]
    with pytest.raises(RulesError) as info:
        recommend(100, rules=rules)
    assert info.value.code == "rules_error"


def test_typed_errors_share_a_base_class_and_codes() -> None:
    assert issubclass(InvalidAgeError, RecommenderError)
    assert issubclass(InvalidInputError, RecommenderError)
    assert issubclass(RecommenderError, ValueError)
    with pytest.raises(InvalidAgeError) as info:
        recommend(-5)
    assert info.value.code == "invalid_age"


def test_valid_age_range_comes_from_the_rules() -> None:
    rules = copy.deepcopy(load_rules())
    rules["valid_age_months"]["max"] = 100
    with pytest.raises(InvalidAgeError):
        recommend(101, rules=rules)
    assert recommend(100, rules=rules)["fluoride"]["band_id"] == "school_84_216m"


def test_product_output_does_not_share_state_with_the_catalogue() -> None:
    products = copy.deepcopy(load_products()["products"])
    snapshot = copy.deepcopy(products)
    result = recommend(48, products=products)
    result["products"][0]["matches_candidate_ids"].append("tampered")
    assert products == snapshot


def test_products_are_sorted_by_id_regardless_of_catalogue_order() -> None:
    products = list(reversed(load_products()["products"]))
    ids = [p["id"] for p in recommend(48, products=products)["products"]]
    assert ids == sorted(ids)


def test_unknown_inputs_are_rejected_with_invalid_input() -> None:
    bad: list[dict[str, Any]] = [
        {"allergies": "mint"},
        {"allergies": [3]},
        {"prefs": {"halal": 1}},
        {"prefs": {"vegan": True}},
        {"prefs": "halal"},
        {"risk_level": "critical"},
    ]
    for kwargs in bad:
        with pytest.raises(InvalidInputError):
            recommend(48, **kwargs)
