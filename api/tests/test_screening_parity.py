"""Parity proof: the SHARED triage table (backend/triage/tests/cases.json, also run
by pytest in triage/ and by Jest in the app) driven through the HTTP layer gives
exactly the engine's levels, reasons, advice, referral templates, lesions and
error codes. Plus: request models agree with the engine on validity, and a
hypothesis property (red flag + any detections -> emergency) through the API.
"""

from __future__ import annotations

import time
from functools import cache
from typing import Any

import jwt
import pytest
from app.main import create_app
from app.schemas.screening import EvaluateRequest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError
from triage.engine import evaluate as engine_evaluate
from triage.errors import TriageInputError

from .conftest import GUARDIAN, HS_SECRET, ISSUER, seeded_repo
from .screening_support import (
    RED_FLAG_IDS,
    SYMPTOM_IDS,
    evaluate_body,
    rules_for_profile,
    screening_settings,
    shared_cases,
)

URL = "/v1/screening/evaluate"
# Engine checks that a per-field schema cannot express (cross-field rules).
CROSS_FIELD_CODES = frozenset({"DETECTIONS_WITHOUT_PHOTO"})


def _token() -> str:
    now = int(time.time())
    claims = {
        "sub": GUARDIAN,
        "iat": now,
        "exp": now + 3600,
        "aud": "authenticated",
        "iss": ISSUER,
        "role": "authenticated",
    }
    return jwt.encode(claims, HS_SECRET, algorithm="HS256")


@cache
def _client(profile: str) -> TestClient:
    app = create_app(
        settings=screening_settings(API_JWKS_ENABLED=False),
        repo=seeded_repo(),
        configure_logs=False,
        screening_rules=rules_for_profile(profile),
    )
    return TestClient(app)


def _post(profile: str, body: Any) -> Any:
    return _client(profile).post(URL, json=body, headers={"Authorization": f"Bearer {_token()}"})


def _split(case: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    data = dict(case["input"])
    return str(data.pop("params_profile")), data


CASES = shared_cases()


def test_table_is_the_shared_one() -> None:
    assert len(CASES) >= 100
    assert {c["input"]["params_profile"] for c in CASES} == {"test", "test_alt", "unset"}


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_shared_case_through_http(case: dict[str, Any]) -> None:
    profile, body = _split(case)
    resp = _post(profile, body)
    expected = case["expected"]
    if "error" in expected:
        assert resp.status_code == 422, resp.text
        err = resp.json()["error"]
        assert err["code"] == expected["error_code"]
        assert err["engine"] == "triage"
        assert err["fail_safe"]["action"] == "show_referral_screen"
        return
    assert resp.status_code == 200, resp.text
    data = resp.json()
    triage = data["triage"]
    assert triage["level"] == expected["level"]
    assert [r["id"] for r in triage["reasons"]] == expected["reasons_ids"]
    assert triage["advice_ids"] == expected["advice_ids"]
    assert triage["referral_template_id"] == expected["referral_template_id"]
    if "lesions" in expected:
        assert [
            {k: lesion[k] for k in ("detection_index", "dentition", "counted")}
            for lesion in triage["lesions"]
        ] == expected["lesions"]
    # Test params are never clinical: the envelope still says "not validated".
    assert data["rules_status"] != "approved" and data["clinically_validated"] is False
    assert "ADV_DISCLAIMER_NOT_DIAGNOSIS" in data["disclaimer_ids"]
    # And the HTTP result equals a direct engine call (same rules, same params).
    rules = rules_for_profile(profile)
    direct = engine_evaluate(rules.triage_rules, body, rules.triage_params)
    direct.pop("rules_version")
    assert triage == direct


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_request_model_agrees_with_engine_on_validity(case: dict[str, Any]) -> None:
    profile, body = _split(case)
    rules = rules_for_profile(profile)
    try:
        engine_evaluate(rules.triage_rules, body, rules.triage_params)
        engine_code = None
    except TriageInputError as exc:
        engine_code = exc.code
    try:
        EvaluateRequest.model_validate(body)
        model_ok = True
    except ValidationError:
        model_ok = False
    if engine_code is None:
        assert model_ok, "model rejects an input the engine accepts"
    elif engine_code in CROSS_FIELD_CODES:
        assert model_ok  # per-field valid; the engine's cross-field rule rejects it
    else:
        assert not model_ok, f"model accepts an input the engine rejects ({engine_code})"


answers = st.sampled_from(["yes", "no", "unknown"])
detection = st.fixed_dictionaries(
    {
        "class": st.sampled_from(["caries_permanent", "caries_primary"]),
        "confidence": st.floats(min_value=0, max_value=1, allow_nan=False),
    }
)


@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(
    profile=st.sampled_from(["test", "test_alt", "unset"]),
    red_flags=st.sets(st.sampled_from(RED_FLAG_IDS), min_size=1),
    other=st.fixed_dictionaries({sid: answers for sid in SYMPTOM_IDS}),
    detections=st.lists(detection, max_size=12),
    pain=st.one_of(st.integers(min_value=0, max_value=10), st.just("unknown")),
    age=st.one_of(st.none(), st.integers(min_value=0, max_value=300)),
)
def test_property_red_flag_always_emergency_via_api(
    profile: str,
    red_flags: set[str],
    other: dict[str, str],
    detections: list[dict[str, Any]],
    pain: int | str,
    age: int | None,
) -> None:
    syms = {**other, **dict.fromkeys(red_flags, "yes")}
    body = evaluate_body(symptoms=syms, detections=detections, pain=pain, age_months=age)
    resp = _post(profile, body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["triage"]["level"] == "emergency"
    assert data["triage"]["referral_template_id"] == "REF_EMERGENCY"
    assert data["referral"]["cta_id"] == "ADV_REFERRAL_CTA"
    assert "ADV_DISCLAIMER_NOT_DIAGNOSIS" in data["disclaimer_ids"]
    # removing every detection never changes an emergency
    no_det = _post(profile, {**body, "detections": []}).json()
    assert no_det["triage"]["level"] == "emergency"
