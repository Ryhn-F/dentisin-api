"""Property-based tests (hypothesis) for recommend()."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from recommender.recommend import InvalidAgeError, load_products, load_rules, recommend
from recommender.tests.helpers import PKG_DIR, read_json

FIXTURE = read_json(PKG_DIR / "tests" / "cases.json")["product_sets"]["fixture"]
SHIPPED = load_products()["products"]
RULES = load_rules()
CODES = [c["code"] for c in RULES["filters"]["allergen_codes"]]
BRANDS = [p["brand"].casefold() for p in [*FIXTURE, *SHIPPED]]

ages = st.integers(min_value=0, max_value=216)
allergies = st.lists(st.sampled_from([*CODES, "latex", " Cinnamon ", ""]), max_size=4)
prefs = st.fixed_dictionaries({"halal": st.booleans(), "no_sls": st.booleans()})
risks = st.sampled_from([None, "low", "moderate", "high"])
catalogues = st.sampled_from([FIXTURE, SHIPPED, []])


@settings(max_examples=150, deadline=None)
@given(ages, allergies, prefs, risks, catalogues)
def test_mandatory_disclaimer_always_present(
    age: int, alg: list[str], pr: dict[str, bool], risk: str | None, cat: list[dict[str, Any]]
) -> None:
    out = recommend(age, alg, pr, risk, products=cat)
    assert "toothpaste_not_filling" in out["disclaimer_ids"]
    texts = [d["text"] for d in out["disclaimers"]]
    assert "Pasta gigi membantu, tetapi tidak menambal gigi berlubang." in texts
    # A referral call to action is always there too (screening, not diagnosis).
    assert "see_dentist_cta" in out["disclaimer_ids"]


@settings(max_examples=150, deadline=None)
@given(ages, allergies, prefs, risks, catalogues)
def test_deterministic_and_does_not_mutate_inputs(
    age: int, alg: list[str], pr: dict[str, bool], risk: str | None, cat: list[dict[str, Any]]
) -> None:
    alg_before, pr_before, cat_before = list(alg), dict(pr), copy.deepcopy(cat)
    rules_before = copy.deepcopy(RULES)
    first = recommend(age, alg, pr, risk, products=cat)
    second = recommend(age, alg, pr, risk, products=cat)
    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert (alg, pr, cat) == (alg_before, pr_before, cat_before)
    assert rules_before == RULES


@settings(max_examples=150, deadline=None)
@given(ages, allergies, prefs, risks, catalogues)
def test_no_brand_in_rules_derived_part(
    age: int, alg: list[str], pr: dict[str, bool], risk: str | None, cat: list[dict[str, Any]]
) -> None:
    out = recommend(age, alg, pr, risk, products=cat)
    rules_part = json.dumps({k: v for k, v in out.items() if k != "products"}, ensure_ascii=False)
    for brand in BRANDS:
        assert brand not in rules_part.casefold()


@settings(max_examples=150, deadline=None)
@given(ages, allergies, prefs)
def test_returned_products_respect_every_filter_and_the_ppm_options(
    age: int, alg: list[str], pr: dict[str, bool]
) -> None:
    out = recommend(age, alg, pr, products=FIXTURE)
    wanted = {a.strip().lower() for a in alg if a.strip()}
    by_id = {p["id"]: p for p in FIXTURE}
    ranges = [(o["min"], o["max"]) for o in out["fluoride"]["ppm_options"]]
    for item in out["products"]:
        src = by_id[item["id"]]
        assert any(lo <= src["ppm"] <= hi for lo, hi in ranges)
        if wanted:
            assert src["allergens"] is not None
            assert not wanted & set(src["allergens"])
        if pr["halal"]:
            assert src["halal_certified"] is True
        if pr["no_sls"] or "sls" in wanted:
            assert src["contains_sls"] is False
    ids = [p["id"] for p in out["products"]]
    assert ids == sorted(ids)


@settings(max_examples=100, deadline=None)
@given(ages)
def test_ppm_options_never_exceed_ceiling_and_candidates(age: int) -> None:
    out = recommend(age)
    ceiling = RULES["regulatory_ceiling_ppm"]["value"]
    band = next(b for b in RULES["age_bands"] if b["id"] == out["fluoride"]["band_id"])
    assert band["age_months"]["min"] <= age <= band["age_months"]["max"]
    cands = {c["id"]: c for c in band["fluoride_ppm"]["candidates"]}
    for option in out["fluoride"]["ppm_options"]:
        assert option["max"] <= ceiling
        assert option["min"] >= cands[option["candidate_id"]]["ppm_min"]
        assert option["max"] <= cands[option["candidate_id"]]["ppm_max"]


@settings(max_examples=100, deadline=None)
@given(ages)
def test_draft_rules_always_request_review_and_never_hide_it(age: int) -> None:
    out = recommend(age)
    assert out["status"]["needs_review"] is True
    assert out["status"]["reasons"]
    assert out["status"]["generic_message"] is not None


@settings(max_examples=100, deadline=None)
@given(
    st.one_of(
        st.integers(max_value=-1),
        st.integers(min_value=217),
        st.floats(allow_nan=True, allow_infinity=True),
        st.text(max_size=5),
        st.none(),
        st.booleans(),
    )
)
def test_every_age_outside_the_valid_integer_range_raises_typed_error(age: Any) -> None:
    with pytest.raises(InvalidAgeError):
        recommend(age)


@settings(max_examples=100, deadline=None)
@given(st.integers(min_value=0, max_value=215))
def test_age_bands_are_monotonic_in_age(age: int) -> None:
    """Supervision can only turn off (never on) as the child gets older."""
    younger = recommend(age)["fluoride"]["supervision"]["required"]
    older = recommend(age + 1)["fluoride"]["supervision"]["required"]
    assert not (older and not younger)
