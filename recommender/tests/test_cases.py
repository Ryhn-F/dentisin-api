"""Runs the shared table tests/cases.json against the Python reference implementation."""

from __future__ import annotations

from typing import Any

import pytest

from recommender.recommend import RecommenderError, load_products, recommend
from recommender.tests.helpers import PKG_DIR, read_json

DOC = read_json(PKG_DIR / "tests" / "cases.json")
CASES: list[dict[str, Any]] = DOC["cases"]


def _catalogue(name: str) -> list[dict[str, Any]]:
    entry = DOC["product_sets"][name]
    if entry == "products.json":
        products: list[dict[str, Any]] = load_products()["products"]
        return products
    assert isinstance(entry, list)
    return entry


def run_case(case: dict[str, Any]) -> dict[str, Any] | str:
    """Return the recommendation, or the typed error code."""
    inp = {**DOC["input_defaults"], **case["input"]}
    try:
        return recommend(
            inp["age_months"],
            inp["allergies"],
            inp["prefs"],
            inp["risk_level"],
            products=_catalogue(case["product_set"]),
        )
    except RecommenderError as err:
        return err.code


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_case(case: dict[str, Any]) -> None:
    expect = case["expect"]
    result = run_case(case)

    if "error" in expect:
        assert result == expect["error"]
        return

    assert isinstance(result, dict), f"unexpected error {result}"
    fluoride = result["fluoride"]
    # Every non-error answer carries the mandatory sentence and the referral CTA.
    assert "toothpaste_not_filling" in result["disclaimer_ids"]
    assert "Pasta gigi membantu, tetapi tidak menambal gigi berlubang." in [
        d["text"] for d in result["disclaimers"]
    ]
    assert result["status"]["needs_review"] is expect["needs_review"]

    simple = {
        "band_id": fluoride["band_id"],
        "amount_code": fluoride["amount"]["code"],
        "supervision_required": fluoride["supervision"]["required"],
        "decision_pending": fluoride["decision_pending"],
        "product_ids": [p["id"] for p in result["products"]],
        "disclaimer_ids": result["disclaimer_ids"],
        "ppm_options": [[o["min"], o["max"]] for o in fluoride["ppm_options"]],
    }
    for key, actual in simple.items():
        if key in expect:
            assert actual == expect[key], key

    fluoride_notes = {n["id"] for n in fluoride["notes"]}
    top_notes = {n["id"] for n in result["notes"]}
    reasons = set(result["status"]["reasons"])
    for key, pool in (
        ("fluoride_note_ids", fluoride_notes),
        ("top_note_ids", top_notes),
        ("reasons", reasons),
    ):
        assert set(expect.get(f"{key}_include", [])) <= pool, f"{key}_include"
        assert not set(expect.get(f"{key}_exclude", [])) & pool, f"{key}_exclude"
    if "generic_message_present" in expect:
        assert (result["status"]["generic_message"] is not None) is expect[
            "generic_message_present"
        ]


def test_table_is_large_enough_and_covers_the_edges() -> None:
    assert len(CASES) >= 40
    assert len({c["id"] for c in CASES}) == len(CASES), "case ids must be unique"
    ages = {c["input"]["age_months"] for c in CASES}
    for edge in (0, 5, 6, 35, 36, 83, 84, 216, -1, 217):
        assert edge in ages, f"missing band edge {edge}"
    codes = {c["expect"].get("error") for c in CASES}
    assert {"invalid_age", "invalid_input"} <= codes
    for flag in ("halal", "no_sls"):
        assert any(c["input"].get("prefs", {}) and flag in c["input"]["prefs"] for c in CASES)
    assert any(c["input"].get("allergies") for c in CASES)
    assert any(c["product_set"] == "none" for c in CASES)
