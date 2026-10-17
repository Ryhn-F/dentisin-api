"""Release gate for the triage rules. Exit code 1 = NOT releasable.

Usage::

    uv run python -m triage.release_check

Blocks the release while any of the following is true (the normal dev test
suite does NOT run this gate; it uses the test-only params profiles instead):

* ``status`` is not ``APPROVED``;
* any param ``value`` is null or its ``status`` is not ``SET`` (PLACEHOLDER/PROPOSED);
* any rule/param/text block is not reviewed (see "review basis" below) or ``reviewed_at`` is null;
* any ``source`` still contains ``PERLU VERIFIKASI``;
* any contact (emergency numbers) is not ``VERIFIED``;
* too few cases back the expected results (see "review basis" below);
* ``dist/`` is stale;
* any string in the rules contains a forbidden phrase.

Review basis (top-level ``review_basis``, ADR 015; absent = ``dentist``):

* ``dentist``: ``reviewed_by`` must not be null/``PENDING_DRG`` (a dentist signs) and at least
  ``release_requirements.min_graded_cases`` cases must be ``graded_by_drg``.
* ``literature``: ``reviewed_by`` may be a team member (any non-empty name that is not a
  placeholder such as ``PENDING_DRG``/``PLACEHOLDER``/``null``), every rule/param ``source`` must
  be filled (the published citation), and at least ``min_graded_cases`` cases must carry an
  ``expected_source`` (the citation for the expected result) without ``PERLU VERIFIKASI``.
  No dentist sign-off is required. Every other check is identical for both bases.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from triage.export import build_artifacts, stale_files
from triage.forbidden import ForbiddenList, lint_document, load_forbidden
from triage.loader import PENDING_REVIEW, load_rules, read_json
from triage.paths import CASES_PATH, DIST_DIR, RULES_PATH

DENTIST = "dentist"
LITERATURE = "literature"
UNVERIFIED_MARKER = "PERLU VERIFIKASI"
# Never accepted as a team reviewer's name on the literature basis (compared case-insensitively).
PLACEHOLDER_REVIEWERS = frozenset(
    {"pending_drg", "placeholder", "pending", "null", "none", "tbd", "todo"}
)


def review_basis(rules: Mapping[str, Any]) -> str:
    """``rules['review_basis']``; a missing field keeps the original dentist gate."""
    return str(rules.get("review_basis", DENTIST))


def reviewer_ok(reviewed_by: object, basis: str) -> bool:
    """Whether ``reviewed_by`` counts as a review on ``basis``."""
    if basis == LITERATURE:
        name = reviewed_by.strip() if isinstance(reviewed_by, str) else ""
        return bool(name) and name.casefold() not in PLACEHOLDER_REVIEWERS
    return reviewed_by not in PENDING_REVIEW


def _review_blockers(
    kind: str, ident: str, item: Mapping[str, Any], basis: str, *, cited: bool = True
) -> list[str]:
    blockers: list[str] = []
    if not reviewer_ok(item["reviewed_by"], basis):
        if basis == LITERATURE:
            blockers.append(
                f"{kind} '{ident}': reviewed_by is not a named team reviewer (literature basis)"
            )
        else:
            blockers.append(f"{kind} '{ident}': not reviewed by a dentist (reviewed_by)")
    if item["reviewed_at"] is None:
        blockers.append(f"{kind} '{ident}': reviewed_at missing")
    if basis == LITERATURE and cited and not item.get("source", "").strip():
        blockers.append(f"{kind} '{ident}': source missing (literature basis needs a citation)")
    if UNVERIFIED_MARKER in item.get("source", ""):
        blockers.append(f"{kind} '{ident}': source still marked PERLU VERIFIKASI")
    return blockers


def _has_expected_source(case: Mapping[str, Any]) -> bool:
    source = case.get("expected_source")
    return isinstance(source, str) and bool(source.strip()) and UNVERIFIED_MARKER not in source


def _case_blockers(needed: int, cases: Mapping[str, Any], basis: str) -> list[str]:
    if basis == LITERATURE:
        cited = sum(1 for case in cases["cases"] if _has_expected_source(case))
        if cited < needed:
            return [f"only {cited} cases carry an expected_source; need >= {needed}"]
        return []
    graded = sum(1 for case in cases["cases"] if case["graded_by_drg"] is True)
    if graded < needed:
        return [f"only {graded} cases graded_by_drg; need >= {needed}"]
    return []


def collect_blockers(
    rules: Mapping[str, Any],
    cases: Mapping[str, Any],
    stale: Sequence[str],
    forbidden: ForbiddenList,
) -> list[str]:
    basis = review_basis(rules)
    blockers: list[str] = []
    if rules["status"] != "APPROVED":
        blockers.append(f"rules status is '{rules['status']}', not 'APPROVED'")
    for name, spec in rules["params"].items():
        if spec["value"] is None or spec["status"] != "SET":
            blockers.append(f"param '{name}': value={spec['value']!r} status={spec['status']}")
        blockers.extend(_review_blockers("param", name, spec, basis))
    for rule in rules["rules"]:
        blockers.extend(_review_blockers("rule", rule["id"], rule, basis))
    # The text review block has no `source` field: it checks wording, not clinical values.
    blockers.extend(
        _review_blockers("texts", "texts_review", rules["texts_review"], basis, cited=False)
    )
    for name, contact in rules["contacts"].items():
        if contact["status"] != "VERIFIED":
            blockers.append(f"contact '{name}': status {contact['status']}")
    blockers.extend(_case_blockers(rules["release_requirements"]["min_graded_cases"], cases, basis))
    blockers.extend(f"dist/{name} is stale" for name in stale)
    blockers.extend(f"forbidden phrase at {hit}" for hit in lint_document(rules, forbidden))
    return blockers


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail unless the triage rules are releasable.")
    parser.add_argument("--rules", type=Path, default=RULES_PATH)
    parser.add_argument("--cases", type=Path, default=CASES_PATH)
    parser.add_argument("--dist", type=Path, default=DIST_DIR)
    parser.add_argument(
        "--forbidden", type=Path, default=None, help="forbidden_phrases.md (default: resolved)"
    )
    args = parser.parse_args(argv)

    rules = load_rules(args.rules)
    blockers = collect_blockers(
        rules,
        read_json(args.cases),
        stale_files(args.dist, build_artifacts(rules)),
        load_forbidden(args.forbidden),
    )
    print(f"review_basis: {review_basis(rules)}")
    if blockers:
        print(f"RELEASE BLOCKED: {len(blockers)} problem(s)")
        for blocker in blockers:
            print(f"  - {blocker}")
        return 1
    print("Release check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
