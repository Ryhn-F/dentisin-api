"""POST /v1/screening/photo (ADR 020): switches, auth/roles, red-flag short-circuit
(detector never runs), fail-closed stages, base64/size limits, quality gate, fake
detector -> triage with boxes only in the overlay, and the LiteRT detector adapter
(reference preprocess/decode with a fake interpreter; no model weights in tests)."""

from __future__ import annotations

import base64
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import anyio
import numpy as np
import pytest
from app.services.detector import Detection, LiteRtDetector, NullDetector, load_detector
from app.services.quality import UnavailableQualityGate
from fastapi.testclient import TestClient

from .conftest import ADMIN, CHILD, GUARDIAN, TEEN, AppFactory, TokenFactory, auth, make_settings
from .screening_support import (
    PHOTO_MARKER,
    ExplodingDetector,
    FakeDetector,
    StubQualityGate,
    mouth_png,
    screening_settings,
    symptoms,
)

URL = "/v1/screening/photo"
BOXES = [
    Detection("caries_primary", 10.0, 20.0, 60.0, 90.0, 0.81),
    Detection("caries_permanent", 100.0, 20.0, 300.0, 200.0, 0.33),
]


def photo_body(photo: bytes | None = None, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "photo": base64.b64encode(photo if photo is not None else mouth_png()).decode("ascii"),
        "symptoms": symptoms(),
        "age_months": 96,
        "pain": 0,
    }
    body.update(overrides)
    return body


def _code(resp: Any) -> str:
    return str(resp.json()["error"]["code"])


def _settings(**overrides: Any) -> Any:
    return screening_settings(ENABLE_SCREENING_PHOTO=True, **overrides)


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


# --- switches / auth / roles ----------------------------------------------------------------


@pytest.mark.parametrize(
    "flags",
    [
        {"ENABLE_SCREENING_API": False, "ENABLE_SCREENING_PHOTO": True},
        {"ENABLE_SCREENING_API": True, "ENABLE_SCREENING_PHOTO": False},
    ],
)
def test_needs_both_switches(
    app_factory: AppFactory, make_token: TokenFactory, flags: dict[str, bool]
) -> None:
    c = TestClient(app_factory(make_settings(**flags), detector=FakeDetector(BOXES)))
    resp = c.post(URL, json=photo_body(), headers=auth(make_token(GUARDIAN)))
    assert resp.status_code == 404 and _code(resp) == "not_found"


def test_auth_and_roles(make_client: Any, make_token: TokenFactory) -> None:
    c = make_client()
    assert c.post(URL, json=photo_body()).status_code == 401
    resp = c.post(URL, json=photo_body(), headers=auth(make_token(CHILD)))
    assert resp.status_code == 403 and _code(resp) == "forbidden"
    for user in (GUARDIAN, TEEN, ADMIN):
        resp = c.post(URL, json=photo_body(), headers=auth(make_token(user)))
        assert resp.status_code == 200, resp.text


# --- pipeline ---------------------------------------------------------------------------------


def test_full_pipeline_boxes_only_in_overlay(
    make_client: Any, make_token: TokenFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Any] = []
    from triage.engine import evaluate as real

    def spy(rules: Any, data: Any, params: Any) -> Any:
        seen.append(json.loads(json.dumps(data)))
        return real(rules, data, params)

    monkeypatch.setattr("app.services.screening.triage_evaluate", spy)
    detector = FakeDetector(BOXES)
    resp = make_client(detector=detector).post(
        URL, json=photo_body(), headers=auth(make_token(GUARDIAN))
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["purpose"] == "screening_photo"
    assert data["outcome"] == "completed" and data["photo_status"] == "assessed"
    assert data["triage"]["level"] == "urgent"
    assert data["referral"]["cta_id"] == "ADV_REFERRAL_CTA"
    assert data["disclaimer_ids"][0] == "ADV_DISCLAIMER_NOT_DIAGNOSIS"
    assert [(s["name"], s["status"]) for s in data["stages"]] == [
        ("red_flag", "completed"),
        ("consent", "skipped"),
        ("photo_fetch", "completed"),
        ("quality_gate", "completed"),
        ("detector", "completed"),
        ("triage", "completed"),
        ("recommendation", "completed"),
    ]
    assert seen[-1]["detections"] == [
        {"class": "caries_primary", "confidence": 0.81},
        {"class": "caries_permanent", "confidence": 0.33},
    ]
    assert data["overlay"]["box_use"] == "ui_overlay_only"
    assert data["overlay"]["detections"][0]["box"] == {
        "x1": 10.0,
        "y1": 20.0,
        "x2": 60.0,
        "y2": 90.0,
    }
    assert data["detector"] == {"name": "fake-test-detector", "model_version": "0.0.0-test"}
    assert detector.seen_bytes == [len(mouth_png())]
    assert PHOTO_MARKER.decode() not in resp.text
    assert base64.b64encode(PHOTO_MARKER).decode() not in resp.text


def test_no_findings_stays_assessed(make_client: Any, make_token: TokenFactory) -> None:
    resp = make_client(detector=FakeDetector([])).post(
        URL, json=photo_body(), headers=auth(make_token(GUARDIAN))
    )
    data = resp.json()
    assert data["photo_status"] == "assessed" and data["overlay"]["detections"] == []
    assert data["triage"]["lesions"] == []


def test_red_flag_short_circuits_without_running_the_detector(
    make_client: Any, make_token: TokenFactory
) -> None:
    detector = FakeDetector(BOXES)
    gate = StubQualityGate(True)
    resp = make_client(detector=detector, gate=gate).post(
        URL,
        json=photo_body(symptoms=symptoms(facial_swelling="yes")),
        headers=auth(make_token(GUARDIAN)),
    )
    data = resp.json()
    assert data["outcome"] == "red_flag_short_circuit" and data["triage"]["level"] == "emergency"
    assert detector.calls == 0 and gate.calls == 0
    assert data["overlay"] is None and data["detector"] is None


def test_quality_failed_skips_detector(make_client: Any, make_token: TokenFactory) -> None:
    detector = FakeDetector(BOXES)
    resp = make_client(detector=detector, gate=StubQualityGate(False)).post(
        URL, json=photo_body(), headers=auth(make_token(GUARDIAN))
    )
    data = resp.json()
    assert data["outcome"] == "quality_failed" and data["photo_status"] == "quality_failed"
    assert data["quality"]["reasons"] == ["blurry"] and detector.calls == 0
    assert data["overlay"] is None


def test_real_reference_quality_gate(app_factory: AppFactory, make_token: TokenFactory) -> None:
    c = TestClient(app_factory(_settings(), detector=FakeDetector([])))
    ok = c.post(URL, json=photo_body(mouth_png()), headers=auth(make_token(GUARDIAN)))
    assert ok.json()["quality"]["passed"] is True, ok.json()["quality"]
    dark = c.post(URL, json=photo_body(mouth_png(dark=True)), headers=auth(make_token(GUARDIAN)))
    assert dark.json()["outcome"] == "quality_failed"


# --- fail closed --------------------------------------------------------------------------------


def test_no_detector_is_503_with_fail_safe(make_client: Any, make_token: TokenFactory) -> None:
    resp = make_client(detector=NullDetector()).post(
        URL, json=photo_body(), headers=auth(make_token(GUARDIAN))
    )
    assert resp.status_code == 503 and _code(resp) == "detector_unavailable"
    assert resp.json()["error"]["fail_safe"]["action"] == "show_referral_screen"


def test_quality_gate_unavailable_is_503(make_client: Any, make_token: TokenFactory) -> None:
    resp = make_client(gate=UnavailableQualityGate()).post(
        URL, json=photo_body(), headers=auth(make_token(GUARDIAN))
    )
    assert resp.status_code == 503 and _code(resp) == "quality_gate_unavailable"


def test_detector_crash_is_503(make_client: Any, make_token: TokenFactory) -> None:
    resp = make_client(detector=ExplodingDetector()).post(
        URL, json=photo_body(), headers=auth(make_token(GUARDIAN))
    )
    assert resp.status_code == 503 and _code(resp) == "detector_failed"
    assert "model crashed" not in resp.text


def test_invalid_base64_is_422_without_fail_safe(
    make_client: Any, make_token: TokenFactory
) -> None:
    resp = make_client().post(
        URL, json=photo_body() | {"photo": "not base64!!"}, headers=auth(make_token(GUARDIAN))
    )
    assert resp.status_code == 422 and _code(resp) == "photo_invalid"
    assert "fail_safe" not in resp.json()["error"]


def test_decoded_photo_over_cap_is_413(make_client: Any, make_token: TokenFactory) -> None:
    resp = make_client().post(
        URL, json=photo_body(b"\xff" * 512_001), headers=auth(make_token(GUARDIAN))
    )
    assert resp.status_code == 413 and _code(resp) == "photo_too_large"


def test_photo_field_over_cap_is_validation_error(
    make_client: Any, make_token: TokenFactory
) -> None:
    resp = make_client().post(
        URL, json=photo_body() | {"photo": "A" * 700_000}, headers=auth(make_token(GUARDIAN))
    )
    assert resp.status_code == 422 and _code(resp) == "validation_error"


def test_body_limit_is_raised_only_for_photo(make_client: Any, make_token: TokenFactory) -> None:
    c = make_client()
    headers = auth(make_token(GUARDIAN))
    big = photo_body(b"\xff" * 200_000)
    assert c.post(URL, json=big, headers=headers).status_code == 200
    resp = c.post("/v1/screening/evaluate", json=big, headers=headers)
    assert resp.status_code == 413 and _code(resp) == "payload_too_large"
    huge = c.post(URL, content=b"{" + b" " * 900_000 + b"}", headers=headers)
    assert huge.status_code == 413


def test_triage_input_errors_carry_engine_codes(make_client: Any, make_token: TokenFactory) -> None:
    c = make_client()
    headers = auth(make_token(GUARDIAN))
    bad_pain = c.post(URL, json=photo_body(pain=11), headers=headers)
    assert bad_pain.status_code == 422
    assert bad_pain.json()["error"]["fail_safe"]["action"] == "show_referral_screen"
    unknown = c.post(URL, json=photo_body(box=[1, 2]), headers=headers)
    assert unknown.status_code == 422 and _code(unknown) == "UNKNOWN_FIELD"
    missing = photo_body()
    del missing["pain"]
    assert _code(c.post(URL, json=missing, headers=headers)) == "MISSING_FIELD"
    not_obj = c.post(URL, json=[1], headers=headers)
    assert not_obj.status_code == 422 and _code(not_obj) == "INPUT_NOT_OBJECT"


# --- LiteRT detector adapter (fake interpreter) -------------------------------------------------

IN = 416
ROWS = sum((IN // s) ** 2 for s in (8, 16, 32))


def _labels(**threshold_overrides: Any) -> dict[str, Any]:
    thresholds = {"caries_permanent": 0.5, "caries_primary": 0.5} | threshold_overrides
    return {
        "model": {"name": "dentisai-caries-detector", "version": "9.9.9-test"},
        "classes": [
            {"id": 1, "name": "caries_primary"},
            {"id": 0, "name": "caries_permanent"},
        ],
        "input": {
            "width": IN,
            "height": IN,
            "color_order": "BGR",
            "normalization": {"pixel_range": [0, 255], "mean": [0, 0, 0], "std": [1, 1, 1]},
            "resize": {
                "method": "letterbox",
                "align": "top_left",
                "pad_value": 114,
                "interpolation": "bilinear",
            },
        },
        "output": {
            "tensors": [{"index": 0, "role": "raw"}],
            "decode": {"type": "yolox_raw", "strides": [8, 16, 32], "sigmoid_applied": False},
            "postprocess": {
                "nms_iou_threshold": 0.45,
                "class_agnostic_nms": False,
                "max_detections": 20,
            },
        },
        "thresholds": {"score": thresholds},
    }


def _raw_with_one_box(seen: list[Any]) -> Any:
    def run(x: Any) -> list[Any]:
        seen.append(x.shape)
        raw = np.full((1, ROWS, 7), -10.0, dtype=np.float32)
        raw[..., :4] = 0.0
        row = 10 * (IN // 8) + 10  # stride 8, gx = gy = 10 -> centre (80, 80) input px
        raw[0, row] = [0.0, 0.0, math.log(4.0), math.log(4.0), 10.0, -10.0, 10.0]
        return [raw]

    return run


def test_litert_detector_decodes_and_maps_back_to_photo_pixels() -> None:
    seen: list[Any] = []
    det = LiteRtDetector(_labels(), _raw_with_one_box(seen))
    found = det.detect_sync(mouth_png(size=(320, 240)))
    assert seen == [(1, IN, IN, 3)]
    assert len(found) == 1
    d = found[0]
    assert d.cls == "caries_primary" and d.score > 0.99
    scale = IN / 320  # letterbox, top-left aligned: no offset
    for got, want in zip((d.x1, d.y1, d.x2, d.y2), (64, 64, 96, 96), strict=True):
        assert got == pytest.approx(want / scale, abs=1e-6)
    assert (det.name, det.model_version) == ("dentisai-caries-detector", "9.9.9-test")


def test_litert_detector_applies_thresholds() -> None:
    det = LiteRtDetector(_labels(caries_primary=0.999999), _raw_with_one_box([]))
    assert det.detect_sync(mouth_png()) == []


def test_litert_detector_rejects_undecodable_photo() -> None:
    det = LiteRtDetector(_labels(), _raw_with_one_box([]))
    with pytest.raises(ValueError, match="JPEG/PNG"):
        det.detect_sync(b"not an image")


def test_litert_detector_requires_every_threshold() -> None:
    with pytest.raises(ValueError, match="threshold"):
        LiteRtDetector(_labels(caries_primary=None), _raw_with_one_box([]))


def test_litert_detector_async_and_output_count() -> None:
    det = LiteRtDetector(_labels(), _raw_with_one_box([]))
    assert len(anyio.run(det.detect, mouth_png())) == 1
    broken = LiteRtDetector(_labels(), lambda x: [])
    with pytest.raises(ValueError, match="output count"):
        broken.detect_sync(mouth_png())


def _bundle(tmp_path: Path, model: bytes = b"tflite-bytes", sha: str | None = None) -> Path:
    (tmp_path / "model.tflite").write_bytes(model)
    labels = _labels()
    labels["model"]["sha256"] = sha if sha is not None else hashlib.sha256(model).hexdigest()
    (tmp_path / "labels.json").write_text(json.dumps(labels), encoding="utf-8")
    return tmp_path


def test_load_detector(tmp_path: Path) -> None:
    def factory(path: Path) -> Any:
        assert path.name == "model.tflite"
        return _raw_with_one_box([])

    assert isinstance(load_detector(None), NullDetector)
    assert isinstance(load_detector(tmp_path / "missing", runner_factory=factory), NullDetector)
    good = load_detector(_bundle(tmp_path), runner_factory=factory)
    assert isinstance(good, LiteRtDetector)
    (tmp_path / "tampered").mkdir()
    tampered = _bundle(tmp_path / "tampered", sha="0" * 64)
    assert isinstance(load_detector(tampered, runner_factory=factory), NullDetector)

    def no_runtime(path: Path) -> Any:
        raise ImportError("no LiteRT")

    assert isinstance(load_detector(_bundle(tmp_path), runner_factory=no_runtime), NullDetector)


def test_create_app_loads_detector_from_settings(
    app_factory: AppFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import detector as mod

    monkeypatch.setattr(
        "app.main.load_detector",
        lambda d: mod.load_detector(d, runner_factory=lambda p: _raw_with_one_box([])),
    )
    app = app_factory(_settings(DETECTOR_MODEL_DIR=str(_bundle(tmp_path))))
    assert isinstance(app.state.dentisai.detector, LiteRtDetector)
