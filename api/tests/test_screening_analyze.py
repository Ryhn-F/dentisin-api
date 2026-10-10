"""POST /v1/screening/analyze: both switches, auth/roles, red-flag short-circuit
(photo never touched), fail-closed stages, consent gating, quality gate (stub and
the real ml/detection reference), fake detector -> triage, boxes only in the
overlay, no photo bytes in logs/responses, audit, rate limit."""

from __future__ import annotations

import io
import json
import logging
from typing import Any

import pytest
from app.logging_setup import JsonFormatter
from app.repositories.memory import InMemoryRepository
from app.services.detector import Detection
from app.services.quality import UnavailableQualityGate
from fastapi.testclient import TestClient

from .conftest import (
    ADMIN,
    CHILD,
    GUARDIAN,
    IMG,
    IMG_EXPIRED,
    IMG_NO_CONSENT,
    IMG_PATH,
    IMG_WITHDRAWN,
    KADER,
    AppFactory,
    TokenFactory,
    auth,
    new_uuid,
)
from .screening_support import (
    PHOTO_MARKER,
    ExplodingDetector,
    FakeDetector,
    StubQualityGate,
    analyze_body,
    mouth_png,
    screening_settings,
    symptoms,
)

URL = "/v1/screening/analyze"
BOXES = [
    Detection("caries_primary", 10.0, 20.0, 60.0, 90.0, 0.81),
    Detection("caries_permanent", 100.0, 20.0, 400.0, 300.0, 0.33),
]


def _code(resp: Any) -> str:
    return str(resp.json()["error"]["code"])


def _stage(data: dict[str, Any], name: str) -> dict[str, Any]:
    return next(s for s in data["stages"] if s["name"] == name)


def _settings(**overrides: Any) -> Any:
    return screening_settings(ENABLE_CLOUD_INFER=True, **overrides)


@pytest.fixture
def make_client(app_factory: AppFactory) -> Any:
    def _make(detector: Any = None, gate: Any = None, settings: Any = None) -> TestClient:
        return TestClient(
            app_factory(
                settings or _settings(),
                detector=detector if detector is not None else FakeDetector(BOXES),
                quality_gate=gate if gate is not None else StubQualityGate(True),
            )
        )

    return _make


def _assert_envelope(data: dict[str, Any]) -> None:
    assert data["rules_status"] == "draft" and data["clinically_validated"] is False
    assert data["disclaimer_ids"][0] == "ADV_DISCLAIMER_NOT_DIAGNOSIS"
    assert "RULES_NOT_VALIDATED" in data["disclaimer_ids"]
    assert data["referral"]["cta_id"] == "ADV_REFERRAL_CTA"
    assert data["purpose"] == "qa_second_opinion"


# --- switches / auth / roles ----------------------------------------------------------------


@pytest.mark.parametrize(
    "flags",
    [
        {"ENABLE_SCREENING_API": False, "ENABLE_CLOUD_INFER": True},
        {"ENABLE_SCREENING_API": True, "ENABLE_CLOUD_INFER": False},
        {"ENABLE_SCREENING_API": False, "ENABLE_CLOUD_INFER": False},
    ],
)
def test_needs_both_switches(
    app_factory: AppFactory, make_token: TokenFactory, flags: dict[str, bool]
) -> None:
    from .conftest import make_settings

    c = TestClient(app_factory(make_settings(**flags), detector=FakeDetector(BOXES)))
    assert c.post(URL, json=analyze_body(IMG)).status_code == 404
    resp = c.post(URL, json=analyze_body(IMG), headers=auth(make_token(ADMIN)))
    assert resp.status_code == 404 and _code(resp) == "not_found"


def test_auth_and_roles(make_client: Any, make_token: TokenFactory) -> None:
    c = make_client()
    assert c.post(URL, json=analyze_body(IMG)).status_code == 401
    expired = c.post(URL, json=analyze_body(IMG), headers=auth(make_token(ADMIN, exp_delta=-99)))
    assert expired.status_code == 401
    forged = c.post(URL, json=analyze_body(IMG), headers=auth(make_token(ADMIN, key="k" * 40)))
    assert forged.status_code == 401
    for user in (GUARDIAN, KADER):
        resp = c.post(URL, json=analyze_body(IMG), headers=auth(make_token(user)))
        assert resp.status_code == 403 and _code(resp) == "forbidden"


# --- red flags first --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "syms",
    [
        symptoms(facial_swelling="yes"),
        symptoms(dental_trauma="yes", night_pain="yes"),
        symptoms(airway_or_swallowing="unknown"),
    ],
)
def test_red_flag_short_circuits_without_touching_the_photo(
    make_client: Any, make_token: TokenFactory, repo: InMemoryRepository, syms: dict[str, str]
) -> None:
    detector = FakeDetector(BOXES)
    gate = StubQualityGate(True)
    # Even an unknown photo reference / disabled dataset flag cannot matter here.
    repo.flags["dataset_contribution"] = False
    c = make_client(detector=detector, gate=gate)
    resp = c.post(
        URL, json=analyze_body(new_uuid(), symptoms=syms), headers=auth(make_token(ADMIN))
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["triage"]["level"] == "emergency"
    assert data["outcome"] == "red_flag_short_circuit" and data["photo_status"] == "not_taken"
    assert _stage(data, "red_flag")["detail"]["short_circuit"] is True
    for name in ("consent", "photo_fetch", "quality_gate", "detector"):
        assert _stage(data, name)["status"] == "skipped"
    assert detector.calls == 0 and gate.calls == 0
    assert repo.audit_log == []  # the photo was never read
    assert data["overlay"] is None and data["quality"] is None and data["detector"] is None
    _assert_envelope(data)


def test_red_flag_short_circuit_even_with_null_detector(
    app_factory: AppFactory, make_token: TokenFactory
) -> None:
    c = TestClient(app_factory(_settings(), quality_gate=UnavailableQualityGate()))
    resp = c.post(
        URL,
        json=analyze_body(IMG, symptoms=symptoms(pus_or_fistula="yes")),
        headers=auth(make_token(ADMIN)),
    )
    assert resp.status_code == 200 and resp.json()["triage"]["level"] == "emergency"


# --- fail closed -------------------------------------------------------------------------------


def test_null_detector_is_503_before_the_photo_is_read(
    app_factory: AppFactory, make_token: TokenFactory, repo: InMemoryRepository
) -> None:
    c = TestClient(app_factory(_settings(), quality_gate=StubQualityGate(True)))
    resp = c.post(URL, json=analyze_body(IMG), headers=auth(make_token(ADMIN)))
    assert resp.status_code == 503 and _code(resp) == "detector_unavailable"
    err = resp.json()["error"]
    assert err["fail_safe"]["action"] == "show_referral_screen"
    assert [s["name"] for s in err["stages"]] == ["red_flag", "detector"]
    assert err["stages"][-1]["status"] == "unavailable"
    assert repo.audit_log == []
    assert "level" not in json.dumps(resp.json())


def test_quality_gate_unavailable_is_503(make_client: Any, make_token: TokenFactory) -> None:
    c = make_client(gate=UnavailableQualityGate())
    resp = c.post(URL, json=analyze_body(IMG), headers=auth(make_token(ADMIN)))
    assert resp.status_code == 503 and _code(resp) == "quality_gate_unavailable"
    assert resp.json()["error"]["stages"][-1] == {
        "name": "quality_gate",
        "status": "unavailable",
        "duration_ms": 0.0,
        "detail": {},
    }


@pytest.mark.parametrize(
    ("detector", "code"),
    [
        (ExplodingDetector(), "detector_failed"),
        (FakeDetector([Detection("tooth", 0, 0, 5, 5, 0.9)]), "detector_output_invalid"),
        (FakeDetector([Detection("caries_primary", 0, 0, 5, 5, 1.5)]), "detector_output_invalid"),
    ],
)
def test_detector_failures_fail_closed(
    make_client: Any, make_token: TokenFactory, detector: Any, code: str
) -> None:
    resp = make_client(detector=detector).post(
        URL, json=analyze_body(IMG), headers=auth(make_token(ADMIN))
    )
    assert resp.status_code == 503 and _code(resp) == code
    assert resp.json()["error"]["stages"][-1]["status"] == "failed"


# --- consent / flag gating -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("photo", "status", "code"),
    [
        (IMG_WITHDRAWN, 409, "image_not_eligible"),
        (IMG_EXPIRED, 409, "image_not_eligible"),
        (IMG_NO_CONSENT, 409, "consent_inactive"),
        ("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", 404, "image_upload_not_found"),
    ],
)
def test_consent_gating(
    make_client: Any, make_token: TokenFactory, photo: str, status: int, code: str
) -> None:
    detector = FakeDetector(BOXES)
    resp = make_client(detector=detector).post(
        URL, json=analyze_body(photo), headers=auth(make_token(ADMIN))
    )
    assert resp.status_code == status and _code(resp) == code
    err = resp.json()["error"]
    assert err["stages"][-1] == {**err["stages"][-1], "name": "consent", "status": "failed"}
    assert err["fail_safe"]["action"] == "show_referral_screen"
    assert detector.calls == 0


def test_consent_withdrawn_after_upload(
    make_client: Any, make_token: TokenFactory, repo: InMemoryRepository
) -> None:
    repo.dataset_consent[CHILD] = False
    resp = make_client().post(URL, json=analyze_body(IMG), headers=auth(make_token(ADMIN)))
    assert resp.status_code == 409 and _code(resp) == "consent_inactive"


def test_dataset_flag_off_is_409(
    make_client: Any, make_token: TokenFactory, repo: InMemoryRepository
) -> None:
    repo.flags["dataset_contribution"] = False
    resp = make_client().post(URL, json=analyze_body(IMG), headers=auth(make_token(ADMIN)))
    assert resp.status_code == 409 and _code(resp) == "dataset_contribution_disabled"


def test_oversized_photo_is_409(
    make_client: Any, make_token: TokenFactory, repo: InMemoryRepository
) -> None:
    repo.photos[IMG_PATH] = b"\xff" * 600_000
    resp = make_client().post(URL, json=analyze_body(IMG), headers=auth(make_token(ADMIN)))
    assert resp.status_code == 409 and _code(resp) == "photo_not_processable"
    assert resp.json()["error"]["stages"][-1]["name"] == "photo_fetch"


# --- full pipeline -----------------------------------------------------------------------------


def test_fake_detector_full_pipeline(
    make_client: Any,
    make_token: TokenFactory,
    repo: InMemoryRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Any] = []
    from triage.engine import evaluate as real

    def spy(rules: Any, data: Any, params: Any) -> Any:
        seen.append(json.loads(json.dumps(data)))
        return real(rules, data, params)

    monkeypatch.setattr("app.services.screening.triage_evaluate", spy)
    detector = FakeDetector(BOXES)
    resp = make_client(detector=detector).post(
        URL, json=analyze_body(IMG, preferences={"no_sls": True}), headers=auth(make_token(ADMIN))
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    _assert_envelope(data)
    assert data["outcome"] == "completed" and data["photo_status"] == "assessed"
    # Shipped placeholder rules: every finding counts, one indication -> urgent.
    assert data["triage"]["level"] == "urgent"
    ids = [r["id"] for r in data["triage"]["reasons"]]
    assert {"DET_CARIES_PRIMARY", "DET_CARIES_PERMANENT", "DET_CARIES_MANY"} <= set(ids)
    assert [s["name"] for s in data["stages"]] == [
        "red_flag",
        "consent",
        "photo_fetch",
        "quality_gate",
        "detector",
        "triage",
        "recommendation",
    ]
    assert all(s["status"] == "completed" for s in data["stages"])
    # Boxes ONLY in the overlay; the engine never saw geometry.
    final_input = seen[-1]
    assert final_input["detections"] == [
        {"class": "caries_primary", "confidence": 0.81},
        {"class": "caries_permanent", "confidence": 0.33},
    ]
    for lesion in data["triage"]["lesions"]:
        assert set(lesion) == {"detection_index", "class", "dentition", "confidence", "counted"}
    overlay = data["overlay"]
    assert overlay["box_use"] == "ui_overlay_only"
    assert overlay["detections"][1]["box"] == {"x1": 100.0, "y1": 20.0, "x2": 400.0, "y2": 300.0}
    assert data["detector"] == {"name": "fake-test-detector", "model_version": "0.0.0-test"}
    assert data["quality"]["passed"] is True
    assert repo.audit_log[-1].action == "screening_analyze_photo_read"
    assert repo.audit_log[-1].details == {"detector": "fake-test-detector"}


def test_box_size_never_changes_the_level(make_client: Any, make_token: TokenFactory) -> None:
    tiny = [Detection("caries_primary", 1.0, 1.0, 2.0, 2.0, 0.6)]
    huge = [Detection("caries_primary", 0.0, 0.0, 4000.0, 3000.0, 0.6)]
    levels = []
    for dets in (tiny, huge):
        resp = make_client(detector=FakeDetector(dets)).post(
            URL, json=analyze_body(IMG), headers=auth(make_token(ADMIN))
        )
        levels.append((resp.json()["triage"]["level"], resp.json()["triage"]["reasons"]))
    assert levels[0] == levels[1]


def test_quality_failed_skips_detector(make_client: Any, make_token: TokenFactory) -> None:
    detector = FakeDetector(BOXES)
    resp = make_client(detector=detector, gate=StubQualityGate(False)).post(
        URL, json=analyze_body(IMG), headers=auth(make_token(ADMIN))
    )
    data = resp.json()
    assert resp.status_code == 200
    assert data["outcome"] == "quality_failed" and data["photo_status"] == "quality_failed"
    assert _stage(data, "detector")["status"] == "skipped" and detector.calls == 0
    assert "CTX_PHOTO_NOT_ASSESSED" in [r["id"] for r in data["triage"]["reasons"]]
    assert data["overlay"] is None and data["quality"]["reasons"] == ["blurry"]


@pytest.mark.parametrize(("dark", "passed"), [(False, True), (True, False)])
def test_real_reference_quality_gate(
    app_factory: AppFactory,
    make_token: TokenFactory,
    repo: InMemoryRepository,
    dark: bool,
    passed: bool,
) -> None:
    repo.photos[IMG_PATH] = mouth_png(dark=dark)
    c = TestClient(app_factory(_settings(), detector=FakeDetector(BOXES)))  # default gate
    data = c.post(URL, json=analyze_body(IMG), headers=auth(make_token(ADMIN))).json()
    assert data["quality"]["passed"] is passed, data["quality"]
    assert data["quality"]["config_status"] == "PLACEHOLDER"
    if not passed:
        assert "too_dark" in data["quality"]["reasons"]


def test_undecodable_photo_is_quality_failed(
    make_client: Any, make_token: TokenFactory, repo: InMemoryRepository, app_factory: AppFactory
) -> None:
    repo.photos[IMG_PATH] = b"\xff\xd8 not really a jpeg " + PHOTO_MARKER
    c = TestClient(app_factory(_settings(), detector=FakeDetector(BOXES)))
    data = c.post(URL, json=analyze_body(IMG), headers=auth(make_token(ADMIN))).json()
    assert data["outcome"] == "quality_failed"
    assert data["quality"]["reasons"] == ["not_decodable"]


def test_validation_uses_engine_codes(make_client: Any, make_token: TokenFactory) -> None:
    c = make_client()
    h = auth(make_token(ADMIN))
    cases: list[tuple[Any, str]] = [
        (analyze_body(IMG, symptoms=symptoms(trismus="kadang")), "INVALID_ANSWER"),
        (analyze_body(IMG, pain=12), "PAIN_INVALID"),
        (analyze_body(IMG, age_months=-3), "AGE_INVALID"),
        ({**analyze_body(IMG), "detections": []}, "UNKNOWN_FIELD"),
        ({**analyze_body(IMG), "image_b64": "/9j/"}, "UNKNOWN_FIELD"),
        ({k: v for k, v in analyze_body(IMG).items() if k != "pain"}, "MISSING_FIELD"),
        ("just a string", "INPUT_NOT_OBJECT"),
        (analyze_body("not-a-uuid"), "validation_error"),
        (analyze_body(IMG, preferences={"halal": 1}), "invalid_input"),
    ]
    for body, code in cases:
        resp = c.post(URL, json=body, headers=h)
        assert resp.status_code == 422 and _code(resp) == code, (body, resp.text)


def test_no_photo_bytes_or_ids_in_logs_or_responses(
    app_factory: AppFactory,
    make_token: TokenFactory,
    repo: InMemoryRepository,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repo.photos[IMG_PATH] = mouth_png()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    token = make_token(ADMIN)
    bodies: list[str] = []
    try:
        with caplog.at_level(logging.DEBUG):
            for detector in (FakeDetector(BOXES), ExplodingDetector()):
                c = TestClient(app_factory(_settings(), detector=detector))
                for body in (
                    analyze_body(IMG, symptoms=symptoms(white_spot="yes")),
                    analyze_body(IMG_NO_CONSENT),
                ):
                    bodies.append(c.post(URL, json=body, headers=auth(token)).text)
    finally:
        root.removeHandler(handler)
    logs = caplog.text + stream.getvalue()
    marker = PHOTO_MARKER.decode()
    for text in [logs, *bodies]:
        assert marker not in text
        assert IMG_PATH not in text and GUARDIAN not in text and CHILD not in text
        assert "iVBOR" not in text  # base64 PNG header
    for fragment in (token, IMG, IMG_NO_CONSENT, ADMIN, "white_spot", "model crashed"):
        assert fragment not in logs, fragment
    assert "/v1/screening/analyze" in logs


def test_rate_limit(app_factory: AppFactory, make_token: TokenFactory) -> None:
    c = TestClient(
        app_factory(
            _settings(API_RATE_LIMIT_PER_MINUTE=1, API_RATE_LIMIT_BURST=1),
            detector=FakeDetector(BOXES),
            quality_gate=StubQualityGate(True),
        )
    )
    h = auth(make_token(ADMIN))
    first = c.post(URL, json=analyze_body(IMG), headers=h)
    second = c.post(URL, json=analyze_body(IMG), headers=h)
    assert first.status_code == 200 and second.status_code == 429
    assert second.headers["retry-after"]
