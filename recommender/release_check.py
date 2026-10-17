"""Release gate: ``python -m recommender.release_check``.

Exits with status 1 while ANY of these holds (this is the point: the shipped data is a
draft until it is reviewed on its review basis):

* a rule/message has ``status`` other than ``approved``,
* ``reviewed_by`` is not a review on the basis (below) or ``reviewed_at`` is null on a
  reviewable row,
* on the literature basis only: any ``source`` is empty or still contains ``PERLU VERIFIKASI``,
* a ppm decision is still pending,
* a product is unverified, synthetic or lacks a BPOM notification number,
* the data does not validate against its JSON Schema.

Review basis (top-level ``review_basis`` in rules.json, ADR 015; absent = ``dentist``):
``dentist`` keeps the original gate (``reviewed_by`` must not be null: a dentist signs);
``literature`` accepts a team member as ``reviewed_by`` (any non-empty name that is not a
placeholder such as ``PENDING_DRG``/``PLACEHOLDER``/``null``) and requires every ``source``
(review rows and ppm candidates) to be a filled citation without ``PERLU VERIFIKASI``.

Development tests do not call this gate, so they stay green on draft data.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

_HERE = Path(__file__).resolve().parent

DENTIST = "dentist"
LITERATURE = "literature"
UNVERIFIED_MARKER = "PERLU VERIFIKASI"
# Never accepted as a team reviewer's name on the literature basis (compared case-insensitively).
PLACEHOLDER_REVIEWERS = frozenset(
    {"pending_drg", "placeholder", "pending", "null", "none", "tbd", "todo"}
)


def review_basis(rules: Any) -> str:
    """``rules['review_basis']``; a missing field keeps the original dentist gate."""
    return str(rules.get("review_basis", DENTIST)) if isinstance(rules, Mapping) else DENTIST


def reviewer_ok(reviewed_by: object, basis: str) -> bool:
    """Whether ``reviewed_by`` counts as a review on ``basis``."""
    if basis == LITERATURE:
        name = reviewed_by.strip() if isinstance(reviewed_by, str) else ""
        return bool(name) and name.casefold() not in PLACEHOLDER_REVIEWERS
    return reviewed_by is not None


def _load(name: str) -> Any:
    return json.loads((_HERE / name).read_text(encoding="utf-8"))


def schema_errors(rules: Any, products: Any) -> list[str]:
    """Return JSON Schema violations for both data files (empty list when valid)."""
    errors: list[str] = []
    for label, data, schema_name in (
        ("rules.json", rules, "schema/rules.schema.json"),
        ("products.json", products, "schema/products.schema.json"),
    ):
        validator = Draft202012Validator(_load(schema_name))
        for err in sorted(validator.iter_errors(data), key=lambda e: list(map(str, e.path))):
            location = "/".join(str(p) for p in err.path) or "<root>"
            errors.append(f"schema:{label}:{location}: {err.message}")
    return errors


def _walk(node: Any, path: str, issues: list[str], basis: str = DENTIST) -> None:
    if isinstance(node, Mapping):
        if "status" in node and "reviewed_by" in node:
            if node["status"] != "approved":
                issues.append(f"{path}: status is {node['status']!r}, not 'approved'")
            if not reviewer_ok(node["reviewed_by"], basis):
                issues.append(
                    f"{path}: reviewed_by is not a named team reviewer (literature basis)"
                    if basis == LITERATURE
                    else f"{path}: reviewed_by is null"
                )
            if node["reviewed_at"] is None:
                issues.append(f"{path}: reviewed_at is null")
        if basis == LITERATURE and "source" in node:
            source = node["source"]
            if not isinstance(source, str) or not source.strip():
                issues.append(f"{path}: source missing (literature basis needs a citation)")
            elif UNVERIFIED_MARKER in source:
                issues.append(f"{path}: source still marked PERLU VERIFIKASI")
        if node.get("decision_pending") is True:
            issues.append(f"{path}: decision_pending is true")
        for key, value in node.items():
            _walk(value, f"{path}.{key}" if path else str(key), issues, basis)
    elif isinstance(node, Sequence) and not isinstance(node, str):
        for index, value in enumerate(node):
            _walk(value, f"{path}[{index}]", issues, basis)


def collect_issues(rules: Any, products: Any) -> list[str]:
    """Return every release blocker found in the two data files."""
    issues = schema_errors(rules, products)
    _walk(rules, "rules", issues, review_basis(rules))
    for product in products["products"]:
        name = f"products[{product['id']}]"
        if not product["verified"]:
            issues.append(f"{name}: verified is false")
        if product["verified_at"] is None:
            issues.append(f"{name}: verified_at is null")
        if product["bpom_notification_no"] is None:
            issues.append(f"{name}: bpom_notification_no is null")
        if product["synthetic"]:
            issues.append(f"{name}: synthetic placeholder entry")
    return issues


def main(argv: Sequence[str] | None = None) -> int:
    del argv  # no options yet
    rules = _load("rules.json")
    issues = collect_issues(rules, _load("products.json"))
    print(f"review_basis: {review_basis(rules)}")
    if issues:
        print(f"RELEASE BLOCKED: {len(issues)} issue(s)")
        for issue in issues:
            print(f"  - {issue}")
        return 1
    print("release check passed: all rules approved and all products verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
