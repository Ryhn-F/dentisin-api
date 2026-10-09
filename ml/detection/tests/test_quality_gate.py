"""Quality gate on SYNTHETIC numpy images; hand-computed values + port-parity vectors."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

import quality_gate as qg
import quality_gate_vectors as qv

CFG = qg.load_config()


def test_config_is_placeholder() -> None:
    raw = yaml.safe_load(qg.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    assert raw["status"] == "PLACEHOLDER" and raw["reviewed_by"] is None
    assert CFG.status == "PLACEHOLDER"


@pytest.mark.parametrize(("rgb", "y"), [((0, 0, 0), 0), ((255, 255, 255), 255), ((255, 0, 0), 77), ((0, 255, 0), 149), ((0, 0, 255), 29)])
def test_luma_integer(rgb: tuple[int, int, int], y: int) -> None:
    # (77R + 150G + 29B + 128) >> 8
    assert qg.luma(np.array([[rgb]], dtype=np.uint8))[0, 0] == y


def test_working_factor_and_downsample() -> None:
    assert qg.working_factor(240, 320, 320) == 1
    assert qg.working_factor(960, 1280, 320) == 4
    assert qg.working_factor(755, 1001, 320) == 4
    img = np.zeros((5, 5, 3), dtype=np.uint8)
    img[0, 0] = 255
    img[0, 1] = 0
    img[1, 0] = 0
    img[1, 1] = 2  # block sum 257 -> (257 + 2) // 4 = 64
    out = qg.block_downsample(img, 2)
    assert out.shape == (2, 2, 3)  # remainder row/col dropped
    assert out[0, 0, 0] == 64
    assert qg.block_downsample(img, 1) is img
    with pytest.raises(ValueError, match="smaller"):
        qg.block_downsample(np.zeros((1, 1, 3), dtype=np.uint8), 2)


def test_laplacian_hand_values() -> None:
    assert qg.laplacian_variance(np.zeros((2, 2), dtype=np.int64)) == 0.0
    assert qg.laplacian_variance(np.full((5, 5), 7, dtype=np.int64)) == 0.0
    yy, xx = np.mgrid[0:6, 0:6]
    checker = np.where((xx + yy) % 2 == 0, 255, 0).astype(np.int64)
    # every valid Laplacian is +-1020 with zero mean -> variance 1020^2
    assert qg.laplacian_variance(checker) == 1020.0**2
    ramp = np.tile(np.arange(10, dtype=np.int64) * 3, (4, 1))  # linear -> Laplacian 0
    assert qg.laplacian_variance(ramp) == 0.0


def test_brightness_and_roi() -> None:
    g = np.array([[0, 10, 200, 250]], dtype=np.int64)
    mean, dark, bright = qg.brightness_stats(g, 16, 245)
    assert (mean, dark, bright) == (115.0, 0.5, 0.25)
    a = np.arange(100).reshape(10, 10)
    roi = qg.central_roi(a, 0.6)  # offset floor(10*0.4/2) = 2
    assert roi.shape == (6, 6) and roi[0, 0] == 22


def test_input_validation() -> None:
    with pytest.raises(ValueError, match="uint8"):
        qg.evaluate_quality(np.zeros((4, 4, 3), dtype=np.float32), CFG)
    with pytest.raises(ValueError, match="uint8"):
        qg.evaluate_quality(np.zeros((4, 4), dtype=np.uint8), CFG)
    with pytest.raises(ValueError, match="empty"):
        qg.evaluate_quality(np.zeros((0, 4, 3), dtype=np.uint8), CFG)


def test_reason_codes_on_synthetic_scenes() -> None:
    sharp = qv.render(qv.SPECS[[s["id"] for s in qv.SPECS].index("mouth_sharp")]["gen"])
    res = qg.evaluate_quality(sharp, CFG)
    assert res.passed and res.reasons == () and res.config_status == "PLACEHOLDER"
    assert res.metrics["teeth_method"] == "heuristic_color_v1"
    dark = (sharp // 8).astype(np.uint8)
    assert "too_dark" in qg.evaluate_quality(dark, CFG).reasons
    white = np.full((60, 80, 3), 255, dtype=np.uint8)
    assert qg.evaluate_quality(white, CFG).reasons == ("too_bright", "blurry", "teeth_not_visible")
    blurred = qv.box_blur(sharp, 2, 3)
    assert qg.evaluate_quality(blurred, CFG).reasons == ("blurry",)


def test_pure_and_deterministic() -> None:
    img = qv.render({"type": "checker", "w": 40, "h": 30, "cell": 3, "a": [250, 240, 230], "b": [160, 50, 60]})
    before = img.copy()
    a = qg.evaluate_quality(img, CFG)
    b = qg.evaluate_quality(img, CFG)
    assert a == b
    assert np.array_equal(img, before)  # input not mutated


class AlwaysVisible:
    def estimate(self, rgb_working: Any, cfg: qg.QualityConfig) -> qg.TeethVisibility:
        return qg.TeethVisibility(True, 0.99, "learned_stub")


def test_pluggable_teeth_estimator() -> None:
    img = qv.render({"type": "checker", "w": 64, "h": 64, "cell": 8, "a": [200, 200, 200], "b": [40, 40, 40]})
    assert "teeth_not_visible" in qg.evaluate_quality(img, CFG).reasons
    res = qg.evaluate_quality(img, CFG, teeth=AlwaysVisible())
    assert res.passed and res.metrics["teeth_method"] == "learned_stub"


def test_stored_vectors_reproduce() -> None:
    stored = json.loads(qv.VECTORS_PATH.read_text(encoding="utf-8"))
    cfg = qg.QualityConfig.from_dict(stored["config"])
    tol = stored["tolerance"]["abs"]
    seen_reasons: set[str] = set()
    for v in stored["vectors"]:
        res = qg.evaluate_quality(qv.render(v["gen"]), cfg)
        assert res.passed == v["expected"]["passed"], v["id"]
        assert list(res.reasons) == v["expected"]["reasons"], v["id"]
        for k, val in v["expected"]["metrics"].items():
            if isinstance(val, float):
                assert res.metrics[k] == pytest.approx(val, abs=tol), (v["id"], k)
            else:
                assert res.metrics[k] == val, (v["id"], k)
        seen_reasons.update(res.reasons)
    assert seen_reasons == set(qg.REASON_ORDER), "vectors must exercise every reason code"
    assert any(v["expected"]["passed"] for v in stored["vectors"])
    assert any(v["expected"]["metrics"]["factor"] > 1 for v in stored["vectors"])
    # vectors embed a config snapshot equal to the current yaml (regenerate when thresholds change)
    assert stored["config"] == yaml.safe_load(qg.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))


def test_vectors_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    assert qv.main([]) == 0
    assert json.loads(capsys.readouterr().out)["version"] == 1
    monkeypatch.setattr(qv, "VECTORS_PATH", tmp_path / "v.json")
    assert qv.main(["--write"]) == 0 and (tmp_path / "v.json").is_file()
    with pytest.raises(ValueError, match="unknown generator"):
        qv.render({"type": "noise", "w": 1, "h": 1})
    grad = qv.render({"type": "hgradient", "w": 4, "h": 1})
    assert grad[0, :, 0].tolist() == [0, 85, 170, 255]
    soft = qv.render({"type": "mouth_soft", "w": 20, "h": 10, "lip": [1, 2, 3], "tooth": [200, 200, 200], "rect": [0, 0, 1, 1],
                      "period": 4, "drop": 10})
    assert soft[0, :4, 0].tolist() == [190, 195, 200, 195]  # tri = |x%4 - 2| = 2,1,0,1
