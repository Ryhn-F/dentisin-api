"""Release gate for the triage rules. Exit code 1 = NOT releasable.

Usage::

    uv run python -m triage.release_check

Blocks the release while any of the following is true (the normal dev test
suite does NOT run this gate; it uses the test-only params profiles instead):

* ``status`` is not ``APPROVED``;
* any param ``value`` is null or its ``status`` is not ``SET`` (PLACEHOLDER/PROPOSED);
* any rule/param/text block has ``reviewed_by`` null/``PENDING_DRG`` or ``reviewed_at`` null;
* any ``source`` still contains ``PERLU VERIFIKASI``;
* any contact (emergency numbers) is not ``VERIFIED``;
* fewer than ``release_requirements.min_graded_cases`` cases are ``graded_by_drg``;
* ``dist/`` is stale;
* any string in the rules contains a forbidden phrase.
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


def _review_blockers(kind: str, ident: str, item: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    if item["reviewed_by"] in PENDING_REVIEW:
        blockers.append(f"{kind} '{ident}': not reviewed by a dentist (reviewed_by)")
    if item["reviewed_at"] is None:
        blockers.append(f"{kind} '{ident}': reviewed_at missing")
    if "PERLU VERIFIKASI" in item.get("source", ""):
        blockers.append(f"{kind} '{ident}': source still marked PERLU VERIFIKASI")
    return blockers


def collect_blockers(
    rules: Mapping[str, Any],
    cases: Mapping[str, Any],
    stale: Sequence[str],
    forbidden: ForbiddenList,
) -> list[str]:
    blockers: list[str] = []
    if rules["status"] != "APPROVED":
        blockers.append(f"rules status is '{rules['status']}', not 'APPROVED'")
    for name, spec in rules["params"].items():
        if spec["value"] is None or spec["status"] != "SET":
            blockers.append(f"param '{name}': value={spec['value']!r} status={spec['status']}")
        blockers.extend(_review_blockers("param", name, spec))
    for rule in rules["rules"]:
        blockers.extend(_review_blockers("rule", rule["id"], rule))
    blockers.extend(_review_blockers("texts", "texts_review", rules["texts_review"]))
    for name, contact in rules["contacts"].items():
        if contact["status"] != "VERIFIED":
            blockers.append(f"contact '{name}': status {contact['status']}")
    graded = sum(1 for case in cases["cases"] if case["graded_by_drg"] is True)
    needed = rules["release_requirements"]["min_graded_cases"]
    if graded < needed:
        blockers.append(f"only {graded} cases graded_by_drg; need >= {needed}")
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
    if blockers:
        print(f"RELEASE BLOCKED: {len(blockers)} problem(s)")
        for blocker in blockers:
            print(f"  - {blocker}")
        return 1
    print("Release check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
