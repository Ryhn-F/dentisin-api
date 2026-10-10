"""Load the screening rule sets ONCE and work out whether they are releasable.

The API never re-implements clinical logic: it imports the Python REFERENCE
engines (``backend/triage`` and ``backend/recommender``, uv path dependencies)
and uses exactly the artefacts the app bundles:

* triage: ``triage/dist/rules.json`` (checksum-verified against
  ``dist/SHA256SUMS``, then validated by ``triage.loader.validate_rules``) and the
  generated ``dist/input.schema.json`` / ``dist/output.schema.json``;
* recommender: ``recommender/rules.json`` + ``products.json`` (via its loaders).

``rules_status`` comes from the engines' OWN release gates
(``triage.release_check.collect_blockers`` / ``recommender.release_check.collect_issues``)
and the status markers in the files; the API invents no clinical value.
Anything unexpected while evaluating the gates is treated as "blocked" (fail closed).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

from recommender import release_check as recommender_release
from recommender.recommend import load_products as load_recommender_products
from recommender.recommend import load_rules as load_recommender_rules
from triage import release_check as triage_release
from triage.engine import resolve_params
from triage.errors import TriageError
from triage.export import build_artifacts, stale_files
from triage.forbidden import load_forbidden
from triage.loader import load_rules, read_json, validate_rules
from triage.paths import CASES_PATH, DIST_DIR, RULES_PATH

RulesStatus = Literal["draft", "needs_review", "approved"]

# Advice ids the API relies on (must exist in the triage rules' always_advice).
DISCLAIMER_ID: Final = "ADV_DISCLAIMER_NOT_DIAGNOSIS"
REFERRAL_CTA_ID: Final = "ADV_REFERRAL_CTA"
# API-level disclaimer id added while the rules are not approved; its text is the
# triage rules' own ``notice`` (nothing invented here).
RULES_NOT_VALIDATED_ID: Final = "RULES_NOT_VALIDATED"
APPROVED_TRIAGE_STATUS: Final = "APPROVED"


class RulesUnavailableError(Exception):
    """Rule artefacts missing, tampered with or invalid. Screening answers 503."""


@dataclass(frozen=True)
class ParamInfo:
    engine: str
    name: str
    status: str
    placeholder: bool
    reviewed: bool


@dataclass(frozen=True)
class EngineGate:
    releasable: bool
    blocker_count: int
    marker_status: str | None = None


@dataclass(frozen=True)
class ScreeningRules:
    triage_rules: Mapping[str, Any] = field(repr=False)
    triage_params: Mapping[str, Any] = field(repr=False)
    input_schema: Mapping[str, Any] = field(repr=False)
    output_schema: Mapping[str, Any] = field(repr=False)
    recommender_rules: Mapping[str, Any] = field(repr=False)
    recommender_products: tuple[Mapping[str, Any], ...] = field(repr=False)
    triage_gate: EngineGate
    recommender_gate: EngineGate
    rules_status: RulesStatus
    params: tuple[ParamInfo, ...]
    test_params: bool = False

    @property
    def triage_version(self) -> str:
        return str(self.triage_rules["version"])

    @property
    def recommender_version(self) -> str:
        return str(self.recommender_rules["rules_version"])

    @property
    def top_level(self) -> str:
        """Highest-ranked triage level (emergency in rules 0.2.0)."""
        return str(max(self.triage_rules["levels"], key=lambda lv: lv["rank"])["id"])

    def advice_text(self, advice_id: str) -> str:
        for item in self.triage_rules["advice"]:
            if item["id"] == advice_id:
                return str(item["text"])
        raise KeyError(advice_id)  # pragma: no cover - ids are verified at load time

    @property
    def notice(self) -> str:
        return str(self.triage_rules["notice"])


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_sums(dist_dir: Path) -> dict[str, str]:
    sums: dict[str, str] = {}
    for line in (dist_dir / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, _, name = line.strip().partition("  ")
        if digest and name:
            sums[name] = digest
    return sums


def _verified_json(dist_dir: Path, name: str, sums: Mapping[str, str]) -> Any:
    path = dist_dir / name
    if sums.get(name) != _sha256_file(path):
        raise RulesUnavailableError(f"dist/{name} does not match dist/SHA256SUMS")
    return read_json(path)


def _triage_gate(rules: Mapping[str, Any], dist_dir: Path) -> EngineGate:
    try:
        source_rules = load_rules(RULES_PATH)
        blockers = triage_release.collect_blockers(
            source_rules,
            read_json(CASES_PATH),
            stale_files(dist_dir, build_artifacts(source_rules)),
            load_forbidden(None),
        )
        count = len(blockers)
    except (OSError, ValueError, KeyError, TypeError, TriageError):
        count = -1  # gate could not run (e.g. minimal image) -> not releasable
    return EngineGate(
        releasable=count == 0, blocker_count=count, marker_status=str(rules["status"])
    )


def _recommender_gate(rules: Mapping[str, Any], products: Mapping[str, Any]) -> EngineGate:
    try:
        count = len(recommender_release.collect_issues(rules, products))
    except (OSError, ValueError, KeyError, TypeError):
        count = -1
    return EngineGate(releasable=count == 0, blocker_count=count)


def _walk_review_rows(node: Any, path: str, out: list[ParamInfo]) -> None:
    """Every recommender row that carries a review status (status + reviewed_by)."""
    if isinstance(node, Mapping):
        if "status" in node and "reviewed_by" in node:
            out.append(
                ParamInfo(
                    engine="recommender",
                    name=path,
                    status=str(node["status"]),
                    placeholder=node["status"] != "approved"
                    or node.get("decision_pending") is True,
                    reviewed=node["reviewed_by"] is not None and node["reviewed_at"] is not None,
                )
            )
        for key, value in node.items():
            if key != "messages":  # message texts are reviewed as a block, listed below
                _walk_review_rows(value, f"{path}.{key}" if path else str(key), out)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            ident = value.get("id") if isinstance(value, Mapping) else None
            _walk_review_rows(value, f"{path}[{ident if ident else index}]", out)


def _param_infos(triage_rules: Mapping[str, Any], rec_rules: Mapping[str, Any]) -> list[ParamInfo]:
    infos = [
        ParamInfo(
            engine="triage",
            name=name,
            status=str(spec["status"]),
            placeholder=spec["value"] is None or spec["status"] != "SET",
            reviewed=spec["reviewed_by"] not in (None, "PENDING_DRG")
            and spec["reviewed_at"] is not None,
        )
        for name, spec in triage_rules["params"].items()
    ]
    rows: list[ParamInfo] = []
    _walk_review_rows({k: v for k, v in rec_rules.items() if k != "messages"}, "", rows)
    infos.extend(rows)
    messages = rec_rules["messages"]
    pending = [m for m in messages.values() if m.get("status") != "approved"]
    infos.append(
        ParamInfo(
            engine="recommender",
            name="messages",
            status="approved" if not pending else "needs_review",
            placeholder=bool(pending),
            reviewed=not pending,
        )
    )
    return infos


def _status(triage_gate: EngineGate, rec_gate: EngineGate, rec_rules: Any) -> RulesStatus:
    if triage_gate.releasable and rec_gate.releasable:
        return "approved"
    rows: list[ParamInfo] = []
    _walk_review_rows(rec_rules, "", rows)
    if triage_gate.marker_status != APPROVED_TRIAGE_STATUS or any(
        row.status == "draft" for row in rows
    ):
        return "draft"
    return "needs_review"


def load_screening_rules(
    params_overrides: Mapping[str, Any] | None = None, dist_dir: Path = DIST_DIR
) -> ScreeningRules:
    """Load + verify everything the screening routes need.

    ``params_overrides`` is TEST-ONLY (triage/tests/fixtures/params_profiles.json):
    production passes None, i.e. ``resolve_params(rules)`` exactly like the app.
    A runtime built with overrides is never reported as approved.
    """
    try:
        sums = _read_sums(dist_dir)
        triage_rules = validate_rules(_verified_json(dist_dir, "rules.json", sums))
        input_schema = _verified_json(dist_dir, "input.schema.json", sums)
        output_schema = _verified_json(dist_dir, "output.schema.json", sums)
        params = resolve_params(triage_rules, params_overrides)
        rec_rules = load_recommender_rules()
        rec_products = load_recommender_products()
    except RulesUnavailableError:
        raise
    except (OSError, ValueError, KeyError, TypeError, TriageError) as exc:
        raise RulesUnavailableError(type(exc).__name__) from None

    for required in (DISCLAIMER_ID, REFERRAL_CTA_ID):
        if required not in triage_rules["always_advice"]:
            raise RulesUnavailableError(f"triage always_advice lacks {required}")

    triage_gate = _triage_gate(triage_rules, dist_dir)
    rec_gate = _recommender_gate(rec_rules, rec_products)
    status = _status(triage_gate, rec_gate, rec_rules)
    if params_overrides is not None and status == "approved":
        status = "needs_review"  # test params are never clinical
    return ScreeningRules(
        triage_rules=triage_rules,
        triage_params=params,
        input_schema=input_schema,
        output_schema=output_schema,
        recommender_rules=rec_rules,
        recommender_products=tuple(rec_products["products"]),
        triage_gate=triage_gate,
        recommender_gate=rec_gate,
        rules_status=status,
        params=tuple(_param_infos(triage_rules, rec_rules)),
        test_params=params_overrides is not None,
    )
