"""Release gate for ``brushing_rules.yaml``. Exit code 1 = NOT releasable.

    python release_check.py [--rules brushing_rules.yaml]

Blocks the release while any of the following is true (the normal test suite does NOT run
this gate against the shipped file; the placeholders there are expected):

* the file does not load (``rules_loader`` structure and value checks);
* the top-level ``status`` or any param ``status`` is ``PLACEHOLDER``;
* ``reviewed_by`` is not a review on the review basis (below) or ``reviewed_at`` is null;
* any param ``source`` or ``rationale`` still contains ``PERLU VERIFIKASI``.

Review basis (top-level ``review_basis``, ADR 015; absent = ``dentist``):

* ``dentist``: ``reviewed_by`` must not be null/``PENDING_DRG`` (a dentist signs).
* ``literature``: ``reviewed_by`` may be a team member (any non-empty name that is not a
  placeholder such as ``PENDING_DRG``/``PLACEHOLDER``/``null``) and every param needs a filled
  ``source`` (the published citation). No dentist sign-off is required.

The scorer is game feedback only (no clinical claim); this gate checks provenance only.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from rules_loader import DEFAULT_RULES_PATH, VALID_STATUSES, rules_from_mapping

DENTIST = "dentist"
LITERATURE = "literature"
UNVERIFIED_MARKER = "PERLU VERIFIKASI"
PENDING_REVIEW = (None, "PENDING_DRG")
# Never accepted as a team reviewer's name on the literature basis (compared case-insensitively).
PLACEHOLDER_REVIEWERS = frozenset(
    {"pending_drg", "placeholder", "pending", "null", "none", "tbd", "todo"}
)
RELEASABLE_STATUSES = VALID_STATUSES - {"PLACEHOLDER"}


def review_basis(data: Mapping[str, Any]) -> str:
    """``data['review_basis']``; a missing field means the dentist gate."""
    return str(data.get("review_basis", DENTIST))


def reviewer_ok(reviewed_by: object, basis: str) -> bool:
    """Whether ``reviewed_by`` counts as a review on ``basis``."""
    if basis == LITERATURE:
        name = reviewed_by.strip() if isinstance(reviewed_by, str) else ""
        return bool(name) and name.casefold() not in PLACEHOLDER_REVIEWERS
    return reviewed_by not in PENDING_REVIEW


def collect_blockers(data: Mapping[str, Any]) -> list[str]:
    """Every release blocker of a parsed rules file. Raises ``ValueError`` if it does not load."""
    rules = rules_from_mapping(data)
    basis = review_basis(data)
    blockers: list[str] = []
    if data.get("status") not in RELEASABLE_STATUSES:
        blockers.append(f"rules status is {data.get('status')!r}")
    blockers.extend(f"param '{name}': status PLACEHOLDER" for name in rules.placeholder_params())
    if not reviewer_ok(data.get("reviewed_by"), basis):
        blockers.append(
            "reviewed_by is not a named team reviewer (literature basis)"
            if basis == LITERATURE
            else "not reviewed by a dentist (reviewed_by)"
        )
    if data.get("reviewed_at") is None:
        blockers.append("reviewed_at missing")
    for name, entry in data["params"].items():
        source = entry.get("source")
        if basis == LITERATURE and not (isinstance(source, str) and source.strip()):
            blockers.append(f"param '{name}': source missing (literature basis needs a citation)")
        for key in ("source", "rationale"):
            if UNVERIFIED_MARKER in str(entry.get(key) or ""):
                blockers.append(f"param '{name}': {key} still marked PERLU VERIFIKASI")
    return blockers


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail unless brushing_rules.yaml is releasable.")
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES_PATH)
    args = parser.parse_args(argv)
    with args.rules.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, Mapping):
        print("RELEASE BLOCKED: rules file must contain a mapping")
        return 1
    try:
        blockers = collect_blockers(data)
    except ValueError as exc:
        blockers = [f"rules file invalid: {exc}"]
    print(f"review_basis: {review_basis(data)}")
    if blockers:
        print(f"RELEASE BLOCKED: {len(blockers)} problem(s)")
        for blocker in blockers:
            print(f"  - {blocker}")
        return 1
    print("Release check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
