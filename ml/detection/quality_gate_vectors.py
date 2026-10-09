"""Parametric synthetic images + reference outputs for quality-gate port parity.

Each vector describes its input with a small integer-only generator (portable to TypeScript)
and stores the expected metrics computed by quality_gate.py with the config snapshot embedded
in the vectors file (so later threshold changes do not silently change the vectors).

    python quality_gate_vectors.py --write    # regenerate tests/quality_gate_vectors.json

Generators (x = column, y = row, all integer arithmetic):
- uniform:   every pixel = rgb
- checker:   ((x // cell) + (y // cell)) % 2 == 0 ? a : b
- hgradient: R = G = B = (x * 255) // (w - 1)
- mouth:     background = lip rgb; inside rect [x0, y0, x1, y1) (fractions of w/h, floored):
             tooth rgb, except columns with (x - X0) % period < gap -> gap rgb
- mouth_soft: like mouth, but inside the rect each channel = tooth - (drop * t) // (period // 2)
             with t = |((x - X0) % period) - period // 2| (smooth triangle wave, no hard edges)
- optional "blur": {"radius": r, "passes": n} applied after any generator: n passes of a
  (2r+1)x(2r+1) box mean per channel, edge-replicated, integer round-half-up
  ((sum + area // 2) // area). Simulates an out-of-focus photo.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from quality_gate import DEFAULT_CONFIG_PATH, QualityConfig, evaluate_quality

VECTORS_PATH = Path(__file__).resolve().parent / "tests" / "quality_gate_vectors.json"

SPECS: list[dict[str, Any]] = [
    {"id": "uniform_black", "gen": {"type": "uniform", "w": 64, "h": 48, "rgb": [0, 0, 0]}},
    {"id": "uniform_white", "gen": {"type": "uniform", "w": 64, "h": 48, "rgb": [255, 255, 255]}},
    {"id": "uniform_mid", "gen": {"type": "uniform", "w": 64, "h": 48, "rgb": [128, 128, 128]}},
    {"id": "checker_1px", "gen": {"type": "checker", "w": 6, "h": 6, "cell": 1, "a": [255, 255, 255], "b": [0, 0, 0]}},
    {"id": "checker_8px", "gen": {"type": "checker", "w": 64, "h": 64, "cell": 8, "a": [200, 200, 200], "b": [40, 40, 40]}},
    {"id": "hgradient", "gen": {"type": "hgradient", "w": 256, "h": 16}},
    {"id": "big_checker_downsampled", "gen": {"type": "checker", "w": 1280, "h": 960, "cell": 4, "a": [255, 0, 0], "b": [0, 0, 255]}},
    {"id": "mouth_sharp", "gen": {"type": "mouth", "w": 320, "h": 240, "lip": [170, 60, 70], "tooth": [235, 228, 210],
                                  "gap": [70, 25, 30], "rect": [0.25, 0.35, 0.75, 0.65], "period": 16, "gap_w": 2}},
    {"id": "mouth_soft", "gen": {"type": "mouth_soft", "w": 320, "h": 240, "lip": [170, 60, 70], "tooth": [235, 228, 210],
                                 "rect": [0.25, 0.35, 0.75, 0.65], "period": 32, "drop": 30}},
    {"id": "mouth_blurred", "gen": {"type": "mouth", "w": 160, "h": 120, "lip": [170, 60, 70], "tooth": [235, 228, 210],
                                    "gap": [70, 25, 30], "rect": [0.25, 0.35, 0.75, 0.65], "period": 16, "gap_w": 2,
                                    "blur": {"radius": 2, "passes": 3}}},
    {"id": "mouth_dark", "gen": {"type": "mouth", "w": 320, "h": 240, "lip": [30, 10, 12], "tooth": [45, 44, 40],
                                 "gap": [10, 4, 5], "rect": [0.25, 0.35, 0.75, 0.65], "period": 16, "gap_w": 2}},
    {"id": "mouth_large_odd", "gen": {"type": "mouth", "w": 1001, "h": 755, "lip": [180, 70, 80], "tooth": [240, 235, 220],
                                      "gap": [60, 20, 25], "rect": [0.2, 0.3, 0.8, 0.7], "period": 40, "gap_w": 5}},
]


def box_blur(img: np.ndarray, radius: int, passes: int) -> np.ndarray:
    out = img.astype(np.int64)
    k = 2 * radius + 1
    area = k * k
    for _ in range(passes):
        pad = np.pad(out, ((radius, radius), (radius, radius), (0, 0)), mode="edge")
        acc = np.zeros_like(out)
        for dy in range(k):
            for dx in range(k):
                acc += pad[dy : dy + out.shape[0], dx : dx + out.shape[1]]
        out = (acc + area // 2) // area
    return out.astype(np.uint8)


def render(gen: dict[str, Any]) -> np.ndarray:
    img = _render(gen)
    if "blur" in gen:
        img = box_blur(img, int(gen["blur"]["radius"]), int(gen["blur"]["passes"]))
    return img


def _render(gen: dict[str, Any]) -> np.ndarray:
    w, h = int(gen["w"]), int(gen["h"])
    yy, xx = np.mgrid[0:h, 0:w]
    t = gen["type"]
    if t == "uniform":
        return np.broadcast_to(np.array(gen["rgb"], dtype=np.uint8), (h, w, 3)).copy()
    if t == "checker":
        mask = ((xx // gen["cell"]) + (yy // gen["cell"])) % 2 == 0
        return np.where(mask[..., None], np.array(gen["a"], dtype=np.uint8), np.array(gen["b"], dtype=np.uint8)).astype(np.uint8)
    if t == "hgradient":
        v = (xx * 255) // (w - 1)
        return np.repeat(v[..., None], 3, axis=2).astype(np.uint8)
    if t in ("mouth", "mouth_soft"):
        fx0, fy0, fx1, fy1 = gen["rect"]
        x0, y0, x1, y1 = int(w * fx0), int(h * fy0), int(w * fx1), int(h * fy1)
        img = np.broadcast_to(np.array(gen["lip"], dtype=np.int64), (h, w, 3)).copy()
        inside = (xx >= x0) & (xx < x1) & (yy >= y0) & (yy < y1)
        period = int(gen["period"])
        if t == "mouth":
            is_gap = ((xx - x0) % period) < int(gen["gap_w"])
            tooth = np.where(is_gap[..., None], np.array(gen["gap"]), np.array(gen["tooth"]))
        else:
            tri = np.abs(((xx - x0) % period) - period // 2)
            tooth = np.array(gen["tooth"])[None, None, :] - (int(gen["drop"]) * tri[..., None]) // (period // 2)
        img = np.where(inside[..., None], tooth, img)
        return np.clip(img, 0, 255).astype(np.uint8)
    raise ValueError(f"unknown generator {t!r}")


def build_vectors(config_dict: dict[str, Any]) -> dict[str, Any]:
    cfg = QualityConfig.from_dict(config_dict)
    vectors = []
    for spec in SPECS:
        res = evaluate_quality(render(spec["gen"]), cfg)
        vectors.append({"id": spec["id"], "gen": spec["gen"],
                        "expected": {"passed": res.passed, "reasons": list(res.reasons), "metrics": res.metrics}})
    return {"version": 1, "tolerance": {"abs": 1e-9, "rel": 1e-9}, "config": config_dict, "vectors": vectors}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--write", action="store_true")
    args = p.parse_args(argv)
    data = build_vectors(yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")))
    text = json.dumps(data, indent=1) + "\n"
    if args.write:
        VECTORS_PATH.write_text(text, encoding="utf-8", newline="\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
