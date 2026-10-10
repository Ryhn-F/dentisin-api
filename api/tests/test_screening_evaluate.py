"""POST /v1/screening/evaluate: switch, auth, roles, envelope, validation, caps,
engine error codes, recommender integration, logging hygiene, rate limit."""

from __future__ import annotations

import io
import json
import logging
from typing import Any

import jsonschema
import pytest
from app.logging_setup import JsonFormatter
from app.services.screening_rules import RulesUnavailableError, load_screening_rules
from fastapi.testclient import TestClient

from .conftest import (
    ADMIN,
    GUARDIAN,
    KADER,
    NOROLE,
    VIEWER,
    AppFactory,
    TokenFactory,
    auth,
)
from .screening_support import evaluate_body, screening_settings, symptoms

URL = "/v1/screening/evaluate"


def _code(resp: Any) -> str:
    return str(resp.json()["error"]["code"])


@pytest.fixture
def sclient(app_factory: AppFactory) -> TestClient:
    return TestClient(app_factory(screening_settings()))


def _assert_envelope(data: dict[str, Any]) -> None:
    """Fields every successful screening response must carry (FR-42)."""
    assert data["rules_status"] in {"draft", "needs_review", "approved"}
    assert data["rules_status"] == "draft"  # shipped rules are drafts today
    assert data["clinically_validated"] is False
    assert "ADV_DISCLAIMER_NOT_DIAGNOSIS" in data["disclaimer_ids"]
    assert "RULES_NOT_VALIDATED" in data["disclaimer_ids"]
    assert {d["id"] for d in data["disclaimers"]} == set(data["disclaimer_ids"])
    assert data["referral"]["required"] is True
    assert data["referral"]["cta_id"] == "ADV_REFERRAL_CTA"
    assert data["referral"]["template_id"] == data["triage"]["referral_template_id"]
    assert "ADV_REFERRAL_CTA" in data["triage"]["advice_ids"]
    assert data["rules_version"] == {"triage": "0.2.0-draft", "recommender": "0.1.0-draft"}
    assert data["request_id"] and data["request_id"] != "-"


# --- switch / auth / roles -----------------------------------------------------------------


def test_disabled_by_default_is_404_before_auth(
    client: TestClient, make_token: TokenFactory
) -> None:
    assert client.post(URL, json=evaluate_body()).status_code == 404
    resp = client.post(URL, json=evaluate_body(), headers=auth(make_token(ADMIN)))
    assert resp.status_code == 404 and _code(resp) == "not_found"


def test_auth_missing_expired_forged(sclient: TestClient, make_token: TokenFactory) -> None:
    missing = sclient.post(URL, json=evaluate_body())
    assert missing.status_code == 401 and _code(missing) == "unauthorized"
    expired = sclient.post(
        URL, json=evaluate_body(), headers=auth(make_token(GUARDIAN, exp_delta=-120))
    )
    assert expired.status_code == 401 and _code(expired) == "token_expired"
    forged = sclient.post(
        URL, json=evaluate_body(), headers=auth(make_token(GUARDIAN, key="x" * 48))
    )
    assert forged.status_code == 401 and _code(forged) == "invalid_token"


@pytest.mark.parametrize(("user", "status"), [(GUARDIAN, 200), (KADER, 200), (ADMIN, 200)])
def test_screening_roles_allowed(
    sclient: TestClient, make_token: TokenFactory, user: str, status: int
) -> None:
    assert sclient.post(URL, json=evaluate_body(), headers=auth(make_token(user))).status_code == (
        status
    )


@pytest.mark.parametrize("user", [VIEWER, NOROLE])
def test_other_roles_forbidden(sclient: TestClient, make_token: TokenFactory, user: str) -> None:
    resp = sclient.post(URL, json=evaluate_body(), headers=auth(make_token(user)))
    assert resp.status_code == 403 and _code(resp) == "forbidden"


def test_anonymous_user_forbidden(sclient: TestClient, make_token: TokenFactory) -> None:
    token = make_token(GUARDIAN, extra={"is_anonymous": True})
    assert sclient.post(URL, json=evaluate_body(), headers=auth(token)).status_code == 403


# --- happy paths ---------------------------------------------------------------------------


def test_no_findings_with_shipped_placeholder_rules(
    sclient: TestClient, make_token: TokenFactory
) -> None:
    resp = sclient.post(URL, json=evaluate_body(), headers=auth(make_token(GUARDIAN)))
    assert resp.status_code == 200
    data = resp.json()
    _assert_envelope(data)
    # Shipped PLACEHOLDER rules: validated age range is empty -> scheduled minimum.
    assert data["triage"]["level"] == "scheduled"
    assert [r["id"] for r in data["triage"]["reasons"]] == ["CTX_AGE_OUTSIDE_VALIDATED_RANGE"]
    assert data["recommendation_status"] == "ok"
    rec = data["recommendation"]
    assert rec["status"]["needs_review"] is True
    assert "toothpaste_not_filling" in data["disclaimer_ids"]
    assert data["purpose"] == "screening_reference"


def test_triage_part_matches_engine_output_schema(
    sclient: TestClient, make_token: TokenFactory
) -> None:
    schema = sclient.get("/v1/screening/schema/output", headers=auth(make_token(GUARDIAN))).json()
    body = evaluate_body(
        detections=[
            {"class": "caries_primary", "confidence": 0.7},
            {"class": "caries_permanent", "confidence": 0.2},
        ],
        pain="unknown",
        symptoms=symptoms(white_spot="unknown"),
    )
    data = sclient.post(URL, json=body, headers=auth(make_token(GUARDIAN))).json()
    jsonschema.validate(
        {**data["triage"], "rules_version": data["rules_version"]["triage"]}, schema
    )
    assert [lesion["dentition"] for lesion in data["triage"]["lesions"]] == ["primary", "permanent"]
    for lesion in data["triage"]["lesions"]:
        assert set(lesion) == {"detection_index", "class", "dentition", "confidence", "counted"}


def test_red_flag_wins_over_detections(sclient: TestClient, make_token: TokenFactory) -> None:
    body = evaluate_body(
        symptoms=symptoms(facial_swelling="yes"),
        detections=[{"class": "caries_permanent", "confidence": 0.99}] * 5,
    )
    data = sclient.post(URL, json=body, headers=auth(make_token(KADER))).json()
    assert data["triage"]["level"] == "emergency"
    assert data["triage"]["referral_template_id"] == "REF_EMERGENCY"
    _assert_envelope(data)


def test_unknown_answer_is_never_treated_as_no(
    sclient: TestClient, make_token: TokenFactory
) -> None:
    body = evaluate_body(symptoms=symptoms(trismus="unknown"))
    data = sclient.post(URL, json=body, headers=auth(make_token(KADER))).json()
    assert data["triage"]["level"] == "emergency"
    assert "RF_UNKNOWN_ANSWER" in [r["id"] for r in data["triage"]["reasons"]]


def test_integral_float_age_is_accepted_like_the_engine(
    sclient: TestClient, make_token: TokenFactory
) -> None:
    resp = sclient.post(
        URL, json=evaluate_body(age_months=96.0, pain=3.0), headers=auth(make_token(GUARDIAN))
    )
    assert resp.status_code == 200


@pytest.mark.parametrize(
    ("age", "status"),
    [(None, "skipped_age_unknown"), (250, "skipped_age_out_of_range"), (0, "ok")],
)
def test_recommendation_skipped_outside_recommender_age_range(
    sclient: TestClient, make_token: TokenFactory, age: int | None, status: str
) -> None:
    data = sclient.post(
        URL, json=evaluate_body(age_months=age), headers=auth(make_token(GUARDIAN))
    ).json()
    assert data["recommendation_status"] == status
    assert (data["recommendation"] is None) == (status != "ok")
    assert "ADV_DISCLAIMER_NOT_DIAGNOSIS" in data["disclaimer_ids"]


def test_preferences_are_passed_to_the_recommender(
    sclient: TestClient, make_token: TokenFactory
) -> None:
    body = evaluate_body(preferences={"allergies": ["SLS", "durian"], "halal": True})
    data = sclient.post(URL, json=body, headers=auth(make_token(GUARDIAN))).json()
    reasons = data["recommendation"]["status"]["reasons"]
    assert "unknown_allergy:durian" in reasons


# --- validation: engine codes, caps, fail-safe ---------------------------------------------


@pytest.mark.parametrize(
    ("override", "code"),
    [
        (
            {"detections": [{"class": "caries_permanent", "confidence": 0.9, "box": [1, 2, 3, 4]}]},
            "DETECTION_INVALID",
        ),
        (
            {
                "detections": [
                    {
                        "class": "caries_primary",
                        "confidence": 0.5,
                        "x1": 0,
                        "y1": 0,
                        "x2": 10,
                        "y2": 10,
                    }
                ]
            },
            "DETECTION_INVALID",
        ),
        ({"detections": [{"class": "caries_cavitated", "confidence": 0.9}]}, "UNKNOWN_CLASS"),
        ({"detections": [{"class": "caries_primary", "confidence": True}]}, "CONFIDENCE_INVALID"),
        (
            {
                "detections": [{"class": "caries_primary", "confidence": 0.5}],
                "photo_status": "not_taken",
            },
            "DETECTIONS_WITHOUT_PHOTO",
        ),
        ({"symptoms": symptoms(lethargy="maybe")}, "INVALID_ANSWER"),
        ({"pain": "5"}, "PAIN_INVALID"),
        ({"pain": True}, "PAIN_INVALID"),
        ({"age_months": 96.5}, "AGE_INVALID"),
        ({"age_months": "96"}, "AGE_INVALID"),
        ({"reference_frame": None}, "UNKNOWN_FIELD"),
        ({"photo_status": "blurry"}, "PHOTO_STATUS_INVALID"),
    ],
)
def test_engine_error_codes_are_returned_as_typed_422(
    sclient: TestClient, make_token: TokenFactory, override: dict[str, Any], code: str
) -> None:
    resp = sclient.post(URL, json=evaluate_body(**override), headers=auth(make_token(GUARDIAN)))
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == code and err["engine"] == "triage"
    assert err["fail_safe"]["action"] == "show_referral_screen"
    assert err["fail_safe"]["referral_cta_id"] == "ADV_REFERRAL_CTA"
    assert err["fail_safe"]["rules_status"] == "draft"
    assert "level" not in json.dumps(resp.json())  # an error never carries a level


def test_missing_field_and_non_object(sclient: TestClient, make_token: TokenFactory) -> None:
    h = auth(make_token(GUARDIAN))
    body = evaluate_body()
    del body["pain"]
    assert _code(sclient.post(URL, json=body, headers=h)) == "MISSING_FIELD"
    assert _code(sclient.post(URL, json=[1, 2], headers=h)) == "INPUT_NOT_OBJECT"
    bad_json = sclient.post(
        URL, content=b"{not json", headers={**h, "Content-Type": "application/json"}
    )
    assert bad_json.status_code == 422 and _code(bad_json) == "validation_error"


def test_preferences_errors_use_recommender_code(
    sclient: TestClient, make_token: TokenFactory
) -> None:
    h = auth(make_token(GUARDIAN))
    for prefs in ({"halal": "yes"}, {"vegan": True}, {"allergies": "sls"}):
        resp = sclient.post(URL, json=evaluate_body(preferences=prefs), headers=h)
        assert resp.status_code == 422, prefs
        assert _code(resp) == "invalid_input" and resp.json()["error"]["engine"] == "recommender"


def test_size_caps(sclient: TestClient, make_token: TokenFactory, app_factory: AppFactory) -> None:
    h = auth(make_token(GUARDIAN))
    many = [{"class": "caries_primary", "confidence": 0.5}] * 101
    resp = sclient.post(URL, json=evaluate_body(detections=many), headers=h)
    assert resp.status_code == 422 and _code(resp) == "too_many_detections"
    allergies = sclient.post(
        URL, json=evaluate_body(preferences={"allergies": ["a"] * 11}), headers=h
    )
    assert allergies.status_code == 422 and _code(allergies) == "invalid_input"
    long_code = sclient.post(
        URL, json=evaluate_body(preferences={"allergies": ["a" * 65]}), headers=h
    )
    assert long_code.status_code == 422
    small = TestClient(app_factory(screening_settings(API_MAX_BODY_BYTES=1024)))
    big = small.post(URL, json=evaluate_body(detections=many), headers=h)
    assert big.status_code == 413 and _code(big) == "payload_too_large"


def test_rules_unavailable_is_503_with_fail_safe(
    app_factory: AppFactory, make_token: TokenFactory
) -> None:
    c = TestClient(app_factory(screening_settings(), screening_rules=None))
    resp = c.post(URL, json=evaluate_body(), headers=auth(make_token(GUARDIAN)))
    assert resp.status_code == 503 and _code(resp) == "rules_unavailable"
    assert resp.json()["error"]["fail_safe"]["action"] == "show_referral_screen"
    # validation errors without rules fall back to the generic code
    bad = c.post(URL, json={"x": 1}, headers=auth(make_token(GUARDIAN)))
    assert bad.status_code == 422 and _code(bad) == "validation_error"


def test_tampered_dist_is_rejected(tmp_path: Any) -> None:
    from triage.paths import DIST_DIR

    for name in ("rules.json", "input.schema.json", "output.schema.json", "SHA256SUMS"):
        (tmp_path / name).write_bytes((DIST_DIR / name).read_bytes())
    (tmp_path / "rules.json").write_bytes((DIST_DIR / "rules.json").read_bytes() + b" ")
    with pytest.raises(RulesUnavailableError):
        load_screening_rules(dist_dir=tmp_path)
    (tmp_path / "SHA256SUMS").unlink()
    with pytest.raises(RulesUnavailableError):
        load_screening_rules(dist_dir=tmp_path)


def test_rate_limit_applies(app_factory: AppFactory, make_token: TokenFactory) -> None:
    c = TestClient(
        app_factory(screening_settings(API_RATE_LIMIT_PER_MINUTE=1, API_RATE_LIMIT_BURST=2))
    )
    h = auth(make_token(GUARDIAN))
    codes = [c.post(URL, json=evaluate_body(), headers=h).status_code for _ in range(3)]
    assert codes[:2] == [200, 200] and codes[2] == 429


def test_logs_contain_no_screening_inputs_or_results(
    app_factory: AppFactory, make_token: TokenFactory, caplog: pytest.LogCaptureFixture
) -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    token = make_token(GUARDIAN)
    try:
        with caplog.at_level(logging.DEBUG), TestClient(app_factory(screening_settings())) as c:
            c.post(
                URL,
                json=evaluate_body(
                    symptoms=symptoms(facial_swelling="yes"),
                    preferences={"allergies": ["kacang-rahasia"]},
                    age_months=123,
                ),
                headers=auth(token),
            )
            c.post(URL, json=evaluate_body(pain="sakit-rahasia"), headers=auth(token))
    finally:
        root.removeHandler(handler)
    text = caplog.text + stream.getvalue()
    assert "/v1/screening/evaluate" in text  # access line by route template
    for fragment in (
        token,
        GUARDIAN,
        "facial_swelling",
        "kacang-rahasia",
        "sakit-rahasia",
        "emergency",
        "RF_FACIAL_SWELLING",
    ):
        assert fragment not in text, fragment


def test_switch_and_auth_are_checked_before_body_validation(
    client: TestClient, sclient: TestClient
) -> None:
    assert client.post(URL, json={"box": [1, 2]}).status_code == 404  # module off: no hint
    assert sclient.post(URL, json={"box": [1, 2]}).status_code == 401  # no engine detail leaks
