"""Shared fixtures. All data is SYNTHETIC (tiny generated images / annotations); no real photos."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml
from PIL import Image

HERE = Path(__file__).resolve().parent
DETECTION = HERE.parent
GATE_PATH = DETECTION.parent / "data" / "license_gate.yaml"


def write_image(path: Path, w: int = 20, h: int = 10, color: tuple[int, int, int] = (128, 128, 128)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.full((h, w, 3), color, dtype=np.uint8)).save(path)
    return path


@pytest.fixture
def make_image() -> Callable[..., Path]:
    return write_image


@pytest.fixture
def gate() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(GATE_PATH.read_text(encoding="utf-8"))
    return data


@pytest.fixture
def config_dict() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((DETECTION / "configs" / "yolox_nano.yaml").read_text(encoding="utf-8"))
    # Generic fixture = an UNVERIFIED candidate (exercises the licence refusals). The real yolox_nano config
    # (owner_accepted_risk, ADR 014 §5) is tested explicitly in test_license_gate.py / test_export.py.
    data["license_verified"] = False
    data.pop("license_note", None)
    return data


@pytest.fixture
def data_files(tmp_path: Path) -> dict[str, Path]:
    """Synthetic canonical.jsonl + splits.json + split_manifest.json (hash-consistent)."""
    import hashlib

    canonical = tmp_path / "canonical.jsonl"
    canonical.write_text(json.dumps({"image": "a.jpg", "width": 10, "height": 10, "boxes": []}) + "\n", encoding="utf-8")
    sdir = tmp_path / "splits"
    sdir.mkdir()
    splits = sdir / "splits.json"
    splits.write_bytes(b'{"images": {"a.jpg": "train"}}\n')
    (sdir / "split_manifest.json").write_text(json.dumps({"splits_sha256": hashlib.sha256(splits.read_bytes()).hexdigest()}),
                                              encoding="utf-8")
    return {"canonical": canonical, "splits": splits, "root": tmp_path}


@pytest.fixture
def write_cfg(tmp_path: Path, config_dict: dict[str, Any], data_files: dict[str, Path]) -> Callable[..., Path]:
    def _write(**overrides: Any) -> Path:
        cfg = json.loads(json.dumps(config_dict))
        cfg["data"]["canonical"] = str(data_files["canonical"])
        cfg["data"]["splits"] = str(data_files["splits"])
        cfg["data"]["images_root"] = str(data_files["root"])
        for k, v in overrides.items():
            node = cfg
            keys = k.split("__")
            for kk in keys[:-1]:
                node = node[kk]
            node[keys[-1]] = v
        path = tmp_path / "cfg.yaml"
        path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        return path

    return _write


@pytest.fixture
def fake_tflite(tmp_path: Path) -> Callable[[int], Path]:
    def _make(size: int = 64) -> Path:
        p = tmp_path / f"fake_{size}.tflite"
        body = b"\x1c\x00\x00\x00TFL3" + b"\x00" * max(0, size - 8)
        p.write_bytes(body)
        return p

    return _make


@pytest.fixture
def copy_gate(tmp_path: Path) -> Callable[[dict[str, Any] | None], Path]:
    def _copy(patch: dict[str, Any] | None = None) -> Path:
        dst = tmp_path / "gate.yaml"
        if patch is None:
            shutil.copy(GATE_PATH, dst)
        else:
            dst.write_text(yaml.safe_dump(patch), encoding="utf-8")
        return dst

    return _copy
