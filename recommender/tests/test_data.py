"""Tests for the data files, release gate, bundle and the forbidden-phrase lint."""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
import re
from typing import Any

import pytest

from recommender import build_bundle, release_check
from recommender.recommend import load_products, load_rules, recommend
from recommender.tests.helpers import (
    PKG_DIR,
    approve_everything,
    forbidden_phrases,
    iter_strings,
    read_json,
    verify_everything,
)
from recommender.tests.test_cases import CASES, run_case

RULES = load_rules()
PRODUCTS = load_products()

# Distinctive real brand names that must never appear in rules.json or in rules-derived output.
# (A lint list only; the product catalogue itself is the only place brands may live.)
BRAND_DENYLIST = ["pepsodent", "ciptadent", "sensodyne", "colgate", "oral-b", "close up", "closeup"]


# --- schemas ---
def test_shipped_files_match_their_schemas() -> None:
    assert release_check.schema_errors(RULES, PRODUCTS) == []


def test_schema_rejects_approved_rows_without_a_reviewer() -> None:
    bad = copy.deepcopy(RULES)
    bad["messages"]["rinse_little"]["status"] = "approved"
    assert any(
        "reviewed_by" in e or "None" in e for e in release_check.schema_errors(bad, PRODUCTS)
    )


def test_schema_rejects_verified_product_without_bpom_number() -> None:
    bad = copy.deepcopy(PRODUCTS)
    bad["products"][0]["verified"] = True
    assert release_check.schema_errors(RULES, bad)


def test_schema_rejects_unknown_fields_and_missing_review_metadata() -> None:
    bad = copy.deepcopy(RULES)
    del bad["messages"]["rinse_little"]["reviewed_at"]
    bad["surprise"] = 1
    assert len(release_check.schema_errors(bad, PRODUCTS)) >= 2


# --- structural invariants of the rules ---
def test_age_bands_are_contiguous_and_cover_the_valid_range() -> None:
    bands = sorted(RULES["age_bands"], key=lambda b: b["age_months"]["min"])
    assert bands[0]["age_months"]["min"] == RULES["valid_age_months"]["min"]
    assert bands[-1]["age_months"]["max"] == RULES["valid_age_months"]["max"]
    for left, right in itertools.pairwise(bands):
        assert right["age_months"]["min"] == left["age_months"]["max"] + 1
    for band in bands:
        assert band["age_months"]["min"] <= band["age_months"]["max"]


def test_every_message_reference_resolves() -> None:
    messages = RULES["messages"]
    refs: list[str] = list(RULES["mandatory_disclaimer_ids"]) + list(
        RULES["product_note_ids"].values()
    )
    for ids in RULES["risk_note_ids"].values():
        refs += ids
    for band in RULES["age_bands"]:
        refs += band["note_ids"] + [band["amount"]["message_id"], band["generic_message_id"]]
        if band["supervision"]["message_id"]:
            refs.append(band["supervision"]["message_id"])
    assert set(refs) <= set(messages)
    assert set(RULES["risk_note_ids"]) == set(RULES["risk_levels"])


def test_supervision_flag_matches_the_age_threshold() -> None:
    for band in RULES["age_bands"]:
        sup = band["supervision"]
        assert sup["required"] == (band["age_months"]["max"] < sup["until_age_months"])
        assert (sup["message_id"] is not None) == sup["required"]


def test_unresolved_conflict_is_represented_not_settled() -> None:
    for band in RULES["age_bands"]:
        block = band["fluoride_ppm"]
        assert block["status"] == "needs_review"
        assert block["reviewed_by"] is None and block["reviewed_at"] is None
        if len(block["candidates"]) > 1:
            assert block["decision_pending"] is True
            assert block["selected_candidate_id"] is None
    ids = {c["id"] for b in RULES["age_bands"] for c in b["fluoride_ppm"]["candidates"]}
    assert {"guideline_ge1000", "sni_legacy_500_1000"} <= ids


def test_no_reviewer_or_date_is_invented_anywhere() -> None:
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "reviewed_by" in node:
                assert node["reviewed_by"] is None and node["reviewed_at"] is None
                assert node["status"] in {"needs_review", "draft"}
                assert node["rationale"] and node["source"]
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(RULES)


def test_unverified_sources_are_marked() -> None:
    for band in RULES["age_bands"]:
        assert "PERLU VERIFIKASI" in band["fluoride_ppm"]["source"]
    assert "PERLU VERIFIKASI" in RULES["regulatory_ceiling_ppm"]["source"]


# --- brands only in products.json ---
def test_rules_json_contains_no_brand_names() -> None:
    text = (PKG_DIR / "rules.json").read_text(encoding="utf-8").casefold()
    for product in PRODUCTS["products"]:
        assert product["brand"].casefold() not in text
    for brand in BRAND_DENYLIST:
        assert brand not in text


def test_products_are_clearly_placeholders_with_at_least_two_per_ppm_class() -> None:
    by_ppm: dict[int, int] = {}
    for item in PRODUCTS["products"]:
        assert item["synthetic"] is True and item["verified"] is False
        assert item["verified_at"] is None and item["bpom_notification_no"] is None
        assert item["source"] is None
        assert item["brand"].startswith("CONTOH-")
        by_ppm[item["ppm"]] = by_ppm.get(item["ppm"], 0) + 1
    assert set(by_ppm) >= {500, 1000, 1450}
    assert all(count >= 2 for count in by_ppm.values())
    assert len({p["id"] for p in PRODUCTS["products"]}) == len(PRODUCTS["products"])
    assert "cekbpom.pom.go.id" in " ".join(PRODUCTS["how_to_fill"])


# --- forbidden phrases ---
def test_forbidden_phrase_list_was_parsed() -> None:
    phrases = forbidden_phrases()
    assert len(phrases) >= 10
    for expected in ("diagnosis", "pasti", "aman", "100%", "bebas karies", "tidak perlu ke dokter"):
        assert expected in phrases


def _hits(strings: list[str]) -> list[tuple[str, str]]:
    phrases = forbidden_phrases()
    return [(p, s) for s in strings for p in phrases if p in s.casefold()]


def test_rules_strings_are_free_of_forbidden_phrases() -> None:
    assert _hits(list(iter_strings(RULES))) == []


def test_products_strings_are_free_of_forbidden_phrases() -> None:
    assert _hits(list(iter_strings(PRODUCTS))) == []


def test_every_output_string_is_free_of_forbidden_phrases() -> None:
    strings: list[str] = []
    for case in CASES:
        result = run_case(case)
        if isinstance(result, dict):
            strings += list(iter_strings(result, skip_keys=frozenset({"brand"})))
    assert strings
    assert _hits(strings) == []


def test_forbidden_phrase_lint_actually_catches_violations() -> None:
    assert _hits(["Anak Anda menderita karies"]) != []
    assert _hits(["Gigi pasti sehat"]) != []
    assert _hits(["Pasta gigi membantu, tetapi tidak menambal gigi berlubang."]) == []


# --- release check ---
def test_release_check_blocks_the_draft_data() -> None:
    issues = release_check.collect_issues(RULES, PRODUCTS)
    assert issues
    text = "\n".join(issues)
    assert "needs_review" in text and "reviewed_by is null" in text
    assert "decision_pending is true" in text and "verified is false" in text
    assert "bpom_notification_no is null" in text
    assert release_check.main([]) == 1


def test_release_check_passes_only_when_everything_is_signed_off() -> None:
    rules = approve_everything(RULES)
    products = verify_everything(PRODUCTS)
    assert release_check.collect_issues(rules, products) == []
    # One unsigned row is enough to block.
    rules["messages"]["rinse_little"]["reviewed_by"] = None
    assert any("rinse_little" in i for i in release_check.collect_issues(rules, products))


def test_release_check_main_returns_zero_on_clean_data(monkeypatch: pytest.MonkeyPatch) -> None:
    clean_rules = approve_everything(RULES)
    clean_products = verify_everything(PRODUCTS)
    files = {"rules.json": clean_rules, "products.json": clean_products}
    real_load = release_check._load
    monkeypatch.setattr(
        release_check, "_load", lambda name: files[name] if name in files else real_load(name)
    )
    assert release_check.main([]) == 0


# --- release check: review basis (ADR 015) ---
TEAM_REVIEWER = "Team Member (tests only, not a real person)"


def _literature(rules: Any) -> dict[str, Any]:
    """Approved on the literature basis: team reviewer, citations without PERLU VERIFIKASI."""
    out = approve_everything(rules)
    out["review_basis"] = "literature"

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "reviewed_by" in node:
                node["reviewed_by"] = TEAM_REVIEWER
            if "source" in node:
                node["source"] = "SYNTHETIC_CITATION (tests only)"
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(out)
    return out


def test_shipped_rules_have_no_review_basis_yet() -> None:
    assert "review_basis" not in RULES
    assert release_check.review_basis(RULES) == "dentist"


def test_absent_basis_equals_explicit_dentist() -> None:
    for rules in (RULES, approve_everything(RULES)):
        dentist = {**copy.deepcopy(rules), "review_basis": "dentist"}
        assert release_check.collect_issues(dentist, PRODUCTS) == release_check.collect_issues(
            rules, PRODUCTS
        )


def test_dentist_basis_is_unchanged() -> None:
    """Only null reviewers block, and sources are not inspected (the original gate)."""
    rules = {**approve_everything(RULES), "review_basis": "dentist"}
    products = verify_everything(PRODUCTS)
    rules["messages"]["rinse_little"]["source"] = "x [PERLU VERIFIKASI]"
    rules["messages"]["rinse_little"]["reviewed_by"] = "PLACEHOLDER"
    assert release_check.collect_issues(rules, products) == []
    rules["messages"]["rinse_little"]["reviewed_by"] = None
    issues = release_check.collect_issues(rules, products)
    assert "rules.messages.rinse_little: reviewed_by is null" in issues
    assert all(i.startswith("schema:") or "rinse_little" in i for i in issues)


def test_literature_basis_passes_with_team_reviewer_and_citations() -> None:
    assert release_check.collect_issues(_literature(RULES), verify_everything(PRODUCTS)) == []


@pytest.mark.parametrize("reviewer", [None, "", "  ", "PENDING_DRG", "PLACEHOLDER", "null"])
def test_literature_basis_rejects_placeholder_reviewers(reviewer: str | None) -> None:
    rules = _literature(RULES)
    rules["regulatory_ceiling_ppm"]["reviewed_by"] = reviewer
    issues = release_check.collect_issues(rules, verify_everything(PRODUCTS))
    expected = "rules.regulatory_ceiling_ppm: reviewed_by is not a named team reviewer"
    # "" / "  " also break the schema (reviewed_by minLength 1 on approved rows)
    assert any(issue.startswith(expected) for issue in issues)


def test_literature_basis_requires_filled_sources() -> None:
    rules = _literature(RULES)
    products = verify_everything(PRODUCTS)
    rules["valid_age_months"]["source"] = "   "
    candidate = rules["age_bands"][1]["fluoride_ppm"]["candidates"][0]
    candidate["source"] = "Pedoman X [PERLU VERIFIKASI]"
    assert release_check.collect_issues(rules, products) == [
        "rules.valid_age_months: source missing (literature basis needs a citation)",
        "rules.age_bands[1].fluoride_ppm.candidates[0]: source still marked PERLU VERIFIKASI",
    ]


def test_literature_basis_keeps_every_other_check() -> None:
    draft = {**copy.deepcopy(RULES), "review_basis": "literature"}
    text = "\n".join(release_check.collect_issues(draft, PRODUCTS))
    assert "needs_review" in text
    assert "reviewed_by is not a named team reviewer" in text
    assert "source still marked PERLU VERIFIKASI" in text
    assert "decision_pending is true" in text
    assert "verified is false" in text
    assert "bpom_notification_no is null" in text
    bad = {**copy.deepcopy(RULES), "review_basis": "team"}
    assert any("review_basis" in e for e in release_check.schema_errors(bad, PRODUCTS))


def test_release_check_main_prints_the_basis(capsys: pytest.CaptureFixture[str]) -> None:
    assert release_check.main([]) == 1
    assert "review_basis: dentist" in capsys.readouterr().out


# --- bundle ---
def test_bundle_is_deterministic_and_describes_the_draft_state() -> None:
    payload, digest = build_bundle.build()
    assert build_bundle.build() == (payload, digest)
    assert hashlib.sha256(payload).hexdigest() == digest
    bundle = json.loads(payload)
    assert bundle["release_ready"] is False and bundle["release_issue_count"] > 0
    assert bundle["rules"] == RULES and bundle["products"] == PRODUCTS["products"]
    assert re.fullmatch(r"[0-9a-f]{64}", bundle["rules_sha256"])


def test_dist_bundle_is_up_to_date_with_the_sources() -> None:
    payload, digest = build_bundle.build()
    assert (build_bundle.DIST / "recommender.json").read_bytes() == payload, (
        "dist is stale: run python -m recommender.build_bundle"
    )
    expected = f"{digest}  recommender.json\n"
    assert (build_bundle.DIST / "recommender.json.sha256").read_text(encoding="utf-8") == expected
    assert build_bundle.main(["--check"]) == 0


def test_bundle_check_detects_staleness(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setattr(build_bundle, "DIST", tmp_path)
    assert build_bundle.main(["--check"]) == 1
    assert build_bundle.main([]) == 0
    assert build_bundle.main(["--check"]) == 0
    (tmp_path / "recommender.json").write_bytes(b"{}")
    assert build_bundle.main(["--check"]) == 1


def test_recommend_matches_bundled_rules() -> None:
    bundle = read_json(build_bundle.DIST / "recommender.json")
    direct = recommend(48)
    from_bundle = recommend(48, rules=bundle["rules"], products=bundle["products"])
    assert direct == from_bundle
