"""make_golden.py + the checked-in golden runtime fixture (docs/api/examples/detector/golden/golden_runtime.json).

CI has no model.tflite, so the real-model part is checked the other way round: the stored RAW tensor is re-decoded with decode.py and
must give exactly the stored detections (the TypeScript port does the same in docs/api/examples/detector/decode.test.ts)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import make_golden as mg

FIXTURE = Path(__file__).resolve().parents[3] / "docs" / "api" / "examples" / "detector" / "golden" / "golden_runtime.json"


def labels_dict(thr: tuple[float | None, float | None] = (0.6, 0.01)) -> dict[str, Any]:
    return {
        "model": {"version": "0.0.1", "run_id": "r1"},
        "classes": [{"id": 0, "name": "caries_permanent"}, {"id": 1, "name": "caries_primary"}],
        "input": {"width": 416, "height": 416, "resize": {"method": "letterbox", "align": "top_left", "pad_value": 114}},
        "output": {"tensors": [{"shape": [1, 3549, 7]}],
                   "decode": {"type": "yolox_raw", "strides": [8, 16, 32], "sigmoid_applied": False},
                   "postprocess": {"nms_iou_threshold": 0.45, "class_agnostic_nms": False, "max_detections": 20}},
        "thresholds": {"score": {"caries_permanent": thr[0], "caries_primary": thr[1]}},
    }


def test_input_pattern_is_deterministic_and_integer_valued() -> None:
    x = mg.make_input()
    assert x.shape == (1, 416, 416, 3) and x.dtype == np.float32
    assert np.array_equal(x, np.floor(x)) and float(np.min(x)) >= 0 and float(np.max(x)) <= 255
    assert float(x[0, 0, 0, 0]) == 0 and float(x[0, 1, 2, 1]) == (2 * 3 + 1 * 5 + 17) % 256
    assert float(x[0, 208, 208, 0]) == min(255, (208 * 3 + 208 * 5) % 256 + 90)  # inside the blob
    assert mg.sha256_f32(x) == mg.sha256_f32(mg.make_input())


def test_tensor_base64_round_trip() -> None:
    a = (np.arange(12, dtype=np.float32) / np.float32(7)).reshape(3, 4)
    assert np.array_equal(mg.tensor_from_b64(mg.tensor_b64(a), (3, 4)), a)


def test_checked_in_fixture_is_consistent() -> None:
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fx["fixture_version"] == mg.FIXTURE_VERSION and fx["tolerance"] == mg.TOLERANCE
    assert fx["input"]["sha256_float32_le"] == mg.sha256_f32(mg.make_input()), "input pattern changed: regenerate the fixture"
    assert fx["input"]["pattern"] == mg.PATTERN
    raw = mg.tensor_from_b64(fx["output"]["raw_float32_le_base64"], tuple(fx["output"]["shape"]))
    assert raw.shape == (1, 3549, 7) and np.isfinite(raw).all()
    spec = fx["decode"]
    assert spec["class_names"] == ["caries_permanent", "caries_primary"] and spec["sigmoid_applied"] is False
    assert {c["id"] for c in fx["cases"]} == {"bundle_thresholds", "top_k_threshold"}
    for case in fx["cases"]:
        got = mg.detections(raw, spec, case["thresholds"])
        assert len(got) == len(case["expected"]), case["id"]
        for g, e in zip(got, case["expected"], strict=True):
            assert (g["class_id"], g["class"]) == (e["class_id"], e["class"])
            assert g["score"] == pytest.approx(e["score"], abs=1e-9)
            assert g["box_photo_px"] == pytest.approx(e["box_photo_px"], abs=1e-6)
    assert len(fx["cases"][1]["expected"]) >= 2, "the top-K case must exercise NMS and the photo mapping"


def test_build_from_a_bundle_without_a_runtime(tmp_path: Path) -> None:
    (tmp_path / "labels.json").write_text(json.dumps(labels_dict()), encoding="utf-8")
    (tmp_path / "model.tflite").write_bytes(b"not a real model")
    rng = np.random.default_rng(7)
    raw = (rng.normal(size=(1, 3549, 7)) * 0.5 - 1.0).astype(np.float32)
    fx = mg.build(tmp_path, raw)
    assert fx["model"]["sha256"] == hashlib.sha256(b"not a real model").hexdigest() and fx["model"]["version"] == "0.0.1"
    assert fx["cases"][0]["thresholds"] == {"caries_permanent": 0.6, "caries_primary": 0.01}
    top = fx["cases"][1]
    assert top["thresholds"]["caries_permanent"] == top["thresholds"]["caries_primary"] and top["expected"]
    assert all(0 <= d["box_photo_px"][0] <= 4000 and 0 <= d["box_photo_px"][3] <= 3000 for d in top["expected"])
    assert mg.top_k_threshold(raw, fx["decode"], k=10**6) <= top["thresholds"]["caries_permanent"]  # k beyond N -> the lowest score


def test_build_rejects_a_model_whose_output_differs_from_labels(tmp_path: Path) -> None:
    (tmp_path / "labels.json").write_text(json.dumps(labels_dict()), encoding="utf-8")
    with pytest.raises(SystemExit, match=r"labels\.json"):
        mg.build(tmp_path, np.zeros((1, 100, 7), dtype=np.float32))


def test_a_missing_model_file_gives_a_null_hash(tmp_path: Path) -> None:
    (tmp_path / "labels.json").write_text(json.dumps(labels_dict()), encoding="utf-8")
    assert mg.build(tmp_path, np.zeros((1, 3549, 7), dtype=np.float32))["model"]["sha256"] is None
