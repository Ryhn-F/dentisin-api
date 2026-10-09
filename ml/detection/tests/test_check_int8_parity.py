from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import pytest

import check_int8_parity as cp
from decode import Decoded


def float_fn(x: Any) -> list[Any]:
    return [x * 2.0, np.array([x.sum()])]


def quant_fn(step: float) -> Any:
    def run(x: Any) -> list[Any]:
        return [np.round(x * 2.0 / step) * step, np.array([x.sum()])]

    return run


def samples(n: int = 5) -> list[Any]:
    rng = np.random.default_rng(0)
    return [rng.random((1, 4, 4, 3)) for _ in range(n)]


def test_identical_models_pass() -> None:
    rep = cp.compare(float_fn, float_fn, samples())
    assert rep.passed and rep.n_samples == 5
    assert rep.tensors[0].max_abs_err == 0.0 and rep.tensors[0].cosine == pytest.approx(1.0)
    assert rep.detection_agreement is None and rep.to_dict()["passed"] is True


def test_fine_quantization_passes_coarse_fails() -> None:
    fine = cp.compare(float_fn, quant_fn(0.01), samples())
    assert fine.passed and 0 < fine.tensors[0].max_abs_err <= 0.005 + 1e-12
    coarse = cp.compare(float_fn, lambda x: [np.sign(x - 0.5) * 2.0, np.array([x.sum()])], samples(), min_cosine=0.99)
    assert not coarse.passed and "cosine" in coarse.failures[0]


def test_cosine_edge_cases() -> None:
    assert cp.cosine(np.zeros(3), np.zeros(3)) == 1.0
    assert cp.cosine(np.zeros(3), np.ones(3)) is None
    assert cp.cosine(np.array([1.0, 0]), np.array([0, 1.0])) == 0.0
    rep = cp.compare(lambda x: [np.zeros(3)], lambda x: [np.ones(3)], [np.zeros(1)])
    assert not rep.passed and rep.tensors[0].cosine is None
    empty = cp.compare(lambda x: [np.zeros(0)], lambda x: [np.zeros(0)], [np.zeros(1)])
    assert empty.tensors[0].max_abs_err == 0.0


def test_shape_and_count_mismatch() -> None:
    with pytest.raises(ValueError, match="output count"):
        cp.compare(lambda x: [x], lambda x: [x, x], [np.zeros(2)])
    with pytest.raises(ValueError, match="shape"):
        cp.compare(lambda x: [x], lambda x: [x[:1]], [np.zeros(2)])
    with pytest.raises(ValueError, match="no samples"):
        cp.compare(float_fn, float_fn, [])


def det(boxes: Sequence[Sequence[float]], classes: Sequence[int], scores: Sequence[float]) -> Decoded:
    return Decoded(np.asarray(boxes, dtype=float).reshape(-1, 4), np.asarray(classes, dtype=np.int64), np.asarray(scores, dtype=float))


def test_detection_agreement() -> None:
    f = det([[0, 0, 10, 10], [20, 20, 30, 30], [40, 40, 50, 50]], [0, 1, 0], [0.9, 0.8, 0.7])
    q = det([[1, 0, 11, 10], [20, 20, 30, 30], [40, 40, 50, 50]], [0, 0, 0], [0.85, 0.8, 0.6])
    m, diffs = cp.match_detections(f, q)
    # box 0 matched (IoU .818), box 1 class differs -> unmatched, box 2 matched
    assert m == 2 and diffs == pytest.approx([0.05, 0.1])
    assert cp.match_detections(f, det([], [], [])) == (0, [])
    outputs = {"f": f, "q": q}

    def decode(outs: Sequence[Any]) -> Decoded:
        return outputs["f"] if float(np.asarray(outs[0]).ravel()[0]) == 0.0 else outputs["q"]

    rep = cp.compare(lambda x: [np.zeros(2)], lambda x: [np.array([1.0, 0.0])], [np.zeros(1)], decode=decode, min_cosine=-1)
    assert rep.detection_agreement == pytest.approx(2 / 3)
    assert not rep.passed and "agreement" in rep.failures[-1]
    assert rep.mean_score_abs_diff == pytest.approx(0.075)
    none = cp.compare(lambda x: [np.zeros(2)], lambda x: [np.zeros(2)], [np.zeros(1)], decode=lambda o: det([], [], []))
    assert none.detection_agreement is None and none.passed
