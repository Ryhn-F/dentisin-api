"""GET /v1/screening/rules and /schema/{input,output}; rules-status computation;
quality-gate loader fallbacks."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from app.services import quality as quality_mod
from app.services import screening_rules as sr
from app.services.quality import (
    ReferenceQualityGate,
    UnavailableQualityGate,
    load_reference_quality_gate,
)
from fastapi.testclient import TestClient
from triage.loader import read_json
from triage.paths import DIST_DIR

from .conftest import ADMIN, GUARDIAN, KID, NOROLE, RETIRED, TEEN, AppFactory, TokenFactory, auth
from .screening_support import FakeDetector, rules_for_profile, screening_settings

RULES = "/v1/screening/rules"


def _code(resp: Any) -> str:
    return str(resp.json()["error"]["code"])


@pytest.fixture
def sclient(app_factory: AppFactory) -> TestClient:
    return TestClient(app_factory(screening_settings()))


@pytest.mark.parametrize(
    "path", [RULES, "/v1/screening/schema/input", "/v1/screening/schema/output"]
)
def test_metadata_switch_and_auth(
    client: TestClient, sclient: TestClient, make_token: TokenFactory, path: str
) -> None:
    assert client.get(path, headers=auth(make_token(ADMIN))).status_code == 404  # switch off
    assert sclient.get(path).status_code == 401
    assert sclient.get(path, headers=auth(make_token(ADMIN, exp_delta=-60))).status_code == 401
    for user in (NOROLE, RETIRED, KID):
        assert sclient.get(path, headers=auth(make_token(user))).status_code == 403
    for user in (GUARDIAN, TEEN, ADMIN):
        assert sclient.get(path, headers=auth(make_token(user))).status_code == 200


def test_rules_metadata_shows_placeholders(sclient: TestClient, make_token: TokenFactory) -> None:
    data = sclient.get(RULES, headers=auth(make_token(TEEN))).json()
    assert data["rules_status"] == "draft" and data["clinically_validated"] is False
    assert data["rules_version"] == {"triage": "0.2.0-draft", "recommender": "0.1.0-draft"}
    assert [c["id"] for c in data["classes"]] == ["caries_permanent", "caries_primary"]
    assert {c["dataset_label"] for c in data["classes"]} == {"D", "d"}
    assert data["levels"] == ["emergency", "urgent", "scheduled", "routine"]
    triage_params = {p["name"]: p for p in data["parameters"] if p["engine"] == "triage"}
    assert "many_lesions_n" in triage_params
    assert all(p["placeholder"] and not p["reviewed"] for p in triage_params.values())
    assert any(p["engine"] == "recommender" and p["placeholder"] for p in data["parameters"])
    gate = next(p for p in data["parameters"] if p["engine"] == "quality_gate")
    assert gate["status"] == "PLACEHOLDER" and gate["placeholder"] is True
    checks = data["release_checks"]
    assert checks["triage"]["releasable"] is False and checks["triage"]["blocker_count"] > 0
    assert checks["triage"]["marker_status"] == "DRAFT"
    assert checks["recommender"]["releasable"] is False
    assert data["detector"] == {
        "name": "null",
        "available": False,
        "version": "0.0.0-null",
        "config_status": None,
    }
    assert data["quality_gate"]["available"] is True
    assert "RULES_NOT_VALIDATED" in data["disclaimer_ids"]
    # no clinical value is exposed (names/status only)
    assert "if_unset" not in str(data) and "value" not in {k for p in data["parameters"] for k in p}


def test_rules_metadata_with_detector(app_factory: AppFactory, make_token: TokenFactory) -> None:
    c = TestClient(app_factory(screening_settings(), detector=FakeDetector()))
    data = c.get(RULES, headers=auth(make_token(ADMIN))).json()
    assert data["detector"]["available"] is True


@pytest.mark.parametrize("kind", ["input", "output"])
def test_schemas_are_served_verbatim(
    sclient: TestClient, make_token: TokenFactory, kind: str
) -> None:
    resp = sclient.get(f"/v1/screening/schema/{kind}", headers=auth(make_token(GUARDIAN)))
    assert resp.status_code == 200
    assert resp.json() == read_json(DIST_DIR / f"{kind}.schema.json")


def test_metadata_rules_unavailable(app_factory: AppFactory, make_token: TokenFactory) -> None:
    c = TestClient(app_factory(screening_settings(), screening_rules=False))
    for path in (RULES, "/v1/screening/schema/input", "/v1/screening/schema/output"):
        resp = c.get(path, headers=auth(make_token(ADMIN)))
        assert resp.status_code == 503 and _code(resp) == "rules_unavailable"


# --- status computation ----------------------------------------------------------------------


def test_status_branches() -> None:
    ok = sr.EngineGate(True, 0, "APPROVED")
    blocked_approved = sr.EngineGate(False, 3, "APPROVED")
    blocked_draft = sr.EngineGate(False, 3, "DRAFT")
    rec_ok = sr.EngineGate(True, 0)
    rows_review = {"x": {"status": "needs_review", "reviewed_by": None, "reviewed_at": None}}
    rows_draft = {"x": {"status": "draft", "reviewed_by": None, "reviewed_at": None}}
    assert sr._status(ok, rec_ok, rows_review) == "approved"
    assert sr._status(blocked_approved, rec_ok, rows_review) == "needs_review"
    assert sr._status(blocked_approved, rec_ok, rows_draft) == "draft"
    assert sr._status(blocked_draft, rec_ok, rows_review) == "draft"


def test_test_params_are_never_approved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sr, "_status", lambda *_: "approved")
    assert sr.load_screening_rules({}).rules_status == "needs_review"
    assert sr.load_screening_rules().rules_status == "approved"
    assert rules_for_profile("test").test_params is True


def test_release_gates_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_: Any, **__: Any) -> Any:
        raise OSError("missing file")

    monkeypatch.setattr("triage.release_check.collect_blockers", boom)
    monkeypatch.setattr("recommender.release_check.collect_issues", boom)
    rules = sr.load_screening_rules()
    assert rules.triage_gate == sr.EngineGate(False, -1, "DRAFT")
    assert rules.recommender_gate == sr.EngineGate(False, -1, None)
    assert rules.rules_status == "draft"


def test_invalid_rules_or_missing_advice_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def bad(_: Any) -> Any:
        raise ValueError("schema")

    monkeypatch.setattr(sr, "validate_rules", bad)
    with pytest.raises(sr.RulesUnavailableError):
        sr.load_screening_rules()
    monkeypatch.setattr(sr, "validate_rules", lambda data: {**data, "always_advice": []})
    with pytest.raises(sr.RulesUnavailableError):
        sr.load_screening_rules()


def test_param_infos_cover_messages_block() -> None:
    triage: dict[str, Any] = {"params": {}}
    rec = {
        "messages": {"m": {"status": "approved"}},
        "rows": [
            {"id": "a", "status": "approved", "reviewed_by": "drg", "reviewed_at": "2026-01-01"}
        ],
    }
    infos = sr._param_infos(triage, rec)
    assert infos[-1] == sr.ParamInfo("recommender", "messages", "approved", False, True)
    assert infos[0] == sr.ParamInfo("recommender", "rows[a]", "approved", False, True)


@pytest.mark.parametrize(
    ("basis", "reviewer", "reviewed"),
    [
        (None, "PLACEHOLDER", True),  # absent = dentist gate: only null/PENDING_DRG fail
        ("dentist", None, False),
        ("literature", "Tim A", True),  # ADR 015: a named team member counts
        ("literature", "PLACEHOLDER", False),
        ("literature", "  ", False),
    ],
)
def test_param_infos_reviewed_follows_review_basis(
    basis: str | None, reviewer: str | None, reviewed: bool
) -> None:
    row = {"status": "SET", "value": 1, "reviewed_by": reviewer, "reviewed_at": "2026-01-01"}
    triage: dict[str, Any] = {"params": {"p": row}}
    rec: dict[str, Any] = {"messages": {}, "rows": [{**row, "id": "a", "status": "approved"}]}
    if basis is not None:
        triage["review_basis"] = basis
        rec["review_basis"] = basis
    infos = sr._param_infos(triage, rec)
    assert infos[0] == sr.ParamInfo("triage", "p", "SET", False, reviewed)
    assert infos[1] == sr.ParamInfo("recommender", "rows[a]", "approved", False, reviewed)


# --- quality-gate loader ---------------------------------------------------------------------


def test_quality_gate_loader_fallbacks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert isinstance(load_reference_quality_gate(tmp_path), UnavailableQualityGate)
    (tmp_path / "quality_gate.py").write_text("import not_a_real_module_xyz\n", encoding="utf-8")
    monkeypatch.delitem(sys.modules, quality_mod._MODULE_NAME, raising=False)
    assert isinstance(load_reference_quality_gate(tmp_path), UnavailableQualityGate)
    assert quality_mod._MODULE_NAME not in sys.modules
    monkeypatch.setitem(sys.modules, "numpy", None)  # simulate the minimal install
    assert isinstance(load_reference_quality_gate(), UnavailableQualityGate)


def test_reference_gate_rejects_huge_and_odd_images(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = load_reference_quality_gate()
    assert isinstance(gate, ReferenceQualityGate)
    monkeypatch.setattr(quality_mod, "MAX_DECODE_PIXELS", 10)
    from .screening_support import mouth_png

    outcome = gate.check(mouth_png())
    assert outcome.passed is False and outcome.reasons == ("not_decodable",)
    assert gate.check(b"GIF89a....").reasons == ("not_decodable",)


# --- defensive paths ------------------------------------------------------------------------


def test_default_rules_loader_returns_none_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import main

    def unavailable(*_: Any, **__: Any) -> Any:
        raise sr.RulesUnavailableError("missing")

    monkeypatch.setattr(main, "load_screening_rules", unavailable)
    assert main._screening_rules.__wrapped__() is None


def test_recommender_error_maps_to_invalid_input(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.errors import ApiError
    from app.services import screening as svc
    from recommender.recommend import InvalidInputError

    def reject(*_: Any, **__: Any) -> Any:
        raise InvalidInputError("bad")

    monkeypatch.setattr("app.services.screening.recommend", reject)
    with pytest.raises(ApiError) as info:
        svc._run_recommender(rules_for_profile("unset"), 96, None)
    assert info.value.status_code == 422 and info.value.code == "invalid_input"


def test_analyze_validation_without_rules_is_generic(
    app_factory: AppFactory, make_token: TokenFactory
) -> None:
    c = TestClient(
        app_factory(
            screening_settings(ENABLE_CLOUD_INFER=True),
            screening_rules=None,
            detector=FakeDetector(),
        )
    )
    resp = c.post("/v1/screening/analyze", json={"x": 1}, headers=auth(make_token(ADMIN)))
    assert resp.status_code == 422 and _code(resp) == "validation_error"
