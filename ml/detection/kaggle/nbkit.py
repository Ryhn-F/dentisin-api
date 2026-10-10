"""Small helpers shared by the Kaggle/Colab notebooks (``*_kaggle.py``). Stdlib + PyYAML only.

Everything that decides something (licence, metrics, export) lives in the pipeline modules;
this file only finds paths, runs commands, writes run configs and environment records.
"""

from __future__ import annotations

import copy
import csv
import json
import os
import shlex
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

#: Pinned toolchain for conversion / evaluation (Python 3.12 venv). onnx2tf 2.6.9 requires Python >= 3.12
#: and pins these exact versions itself (PyPI metadata read 2026-10-09).
EXPORT_PY = "3.12"
EXPORT_PKGS = ("onnx2tf==2.6.9", "onnx==1.20.1", "onnxruntime==1.26.0", "onnxsim==0.6.5", "ai-edge-litert==2.1.2", "numpy==2.2.6",
               "pillow>=10", "pyyaml>=6", "jsonschema>=4.18", "defusedxml>=0.7.1")
#: onnx2tf's optional "tensorflow" extra (exact pins from its PyPI metadata) for ``-tb tf_converter``: the default
#: flatbuffer_direct INT8 path failed on a tiny YOLOX-like graph (LOGISTIC scale check; Conv->Reshape requantisation).
EXPORT_TF_PKGS = ("tensorflow==2.21.0", "tf-keras==2.21.0", "keras==3.15.0")
#: Model Maker env (Python 3.10). TF 2.15.1 = last release allowed by mediapipe-model-maker 0.2.1.4 (tensorflow<2.16).
MM_PY = "3.10"
MM_PKGS = ("mediapipe-model-maker==0.2.1.4", "tensorflow[and-cuda]==2.15.1")
#: YOLOX training-time deps installed next to the kernel's own torch (YOLOX itself: --no-deps, pinned commit).
YOLOX_TRAIN_PKGS = ("loguru", "tabulate", "pycocotools", "thop", "ninja", "psutil", "tensorboard", "opencv-python-headless")


def compute_env(environ: Mapping[str, str] | None = None, modules: Sequence[str] | None = None) -> str:
    env = os.environ if environ is None else environ
    mods = list(sys.modules) if modules is None else modules
    if any(k.startswith("KAGGLE_") for k in env):
        return "kaggle"
    if "google.colab" in mods or "COLAB_RELEASE_TAG" in env or "COLAB_GPU" in env:
        return "colab"
    return "local"


def default_dirs(env: str, base: Path | None = None) -> dict[str, Path]:
    """``work`` = persisted outputs (Kaggle: saved with a committed version); ``tmp`` = scratch, never exported;
    ``inputs`` = read-only attached data."""
    if env == "kaggle":  # /kaggle/working (20 GB) is saved with a committed version; /tmp is not
        return {"work": Path("/kaggle/working"), "tmp": Path("/tmp/dentisai"), "inputs": Path("/kaggle/input")}
    if env == "colab":
        return {"work": Path("/content/drive/MyDrive/dentisai_runs"), "tmp": Path("/content/tmp"),
                "inputs": Path("/content/drive/MyDrive/dentisai_inputs")}
    root = base or Path.cwd() / "nb_local"
    return {"work": root / "working", "tmp": root / "tmp", "inputs": root / "input"}


def find_dir(roots: Sequence[Path], marker: str, *, max_depth: int = 4) -> Path | None:
    """First directory D under ``roots`` (breadth-first, sorted) such that ``D / marker`` exists."""
    for root in roots:
        frontier = [root] if root.is_dir() else []
        for _ in range(max_depth + 1):
            nxt: list[Path] = []
            for d in frontier:
                if (d / marker).exists():
                    return d
                try:
                    nxt += sorted(p for p in d.iterdir() if p.is_dir() and not p.name.startswith("."))
                except OSError:
                    continue
            frontier = nxt
    return None


def deep_merge(base: Mapping[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(dict(base))
    for k, v in over.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, Mapping) and isinstance(out.get(k), Mapping) else copy.deepcopy(v)
    return out


def write_run_config(base_cfg: Path, out: Path, overrides: Mapping[str, Any]) -> dict[str, Any]:
    """Copy a repo config with notebook-specific paths. Refuses to touch ``license_verified`` or ``data.datasets``:
    licence status is decided by the repo config + license gate, never by a notebook."""
    for forbidden in ("license_verified", "candidate"):
        if forbidden in overrides:
            raise ValueError(f"notebook must not override {forbidden!r}")
    if "datasets" in overrides.get("data", {}):
        raise ValueError("notebook must not override data.datasets")
    cfg = deep_merge(yaml.safe_load(base_cfg.read_text(encoding="utf-8")), overrides)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return cfg


def metadata_from_splits(splits: Path, out_csv: Path, *, source: str = "ahmed2025") -> int:
    """eval.py metadata CSV with ``patient_id`` = split group id (so bootstrap CIs resample groups, not images)."""
    data = json.loads(splits.read_text(encoding="utf-8"))
    groups: Mapping[str, str] = data.get("image_groups") or {}
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["image", "patient_id", "source"])
        for img in sorted(data["images"]):
            w.writerow([img, groups.get(img, ""), source])
    return len(data["images"])


def sha256_file(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def processed_paths(processed: Path) -> dict[str, Path]:
    return {"canonical": processed / "canonical.jsonl", "splits": processed / "splits" / "splits.json",
            "manifest": processed / "splits" / "split_manifest.json"}


def check_processed(processed: Path) -> dict[str, Any] | None:
    """None if the processed files are absent; otherwise verify splits.json against split_manifest.json."""
    p = processed_paths(processed)
    if not all(v.is_file() for v in p.values()):
        return None
    manifest = json.loads(p["manifest"].read_text(encoding="utf-8"))
    actual = sha256_file(p["splits"])
    if manifest.get("splits_sha256") != actual:
        raise ValueError(f"splits.json sha256 {actual} != split_manifest.json {manifest.get('splits_sha256')}")
    return {"canonical_sha256": sha256_file(p["canonical"]), "splits_sha256": actual,
            "group_quality": manifest.get("group_quality"), "image_counts": manifest.get("image_counts")}


def class_map_problems(path: Path) -> list[str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    probs = [f"source label {k!r} is null (undecided)" for k, v in (data.get("map") or {}).items() if v is None]
    if data.get("status") not in ("reviewed", "decided"):  # 'decided' = product-owner decision (ADR 014), no drg review
        probs.append(f"class map status is {data.get('status')!r} (not 'decided'/'reviewed'): runs are technical experiments only")
    return probs


def pick_onnx2tf_outputs(folder: Path) -> dict[str, Path | None]:
    """onnx2tf -oiqt writes <name>_float32.tflite, _integer_quant.tflite (float I/O) and
    _full_integer_quant.tflite (int8 I/O). The float-I/O INT8 file is preferred for the app: the raw YOLOX
    tensor mixes box offsets and probabilities, so an int8 OUTPUT would share one scale for both."""
    out: dict[str, Path | None] = {"float32": None, "int8_float_io": None, "int8_full": None}
    for p in sorted(folder.glob("*.tflite")):
        name = p.name
        if "_with_int16_act" in name:
            continue
        if name.endswith("_full_integer_quant.tflite"):
            out["int8_full"] = out["int8_full"] or p
        elif name.endswith("_integer_quant.tflite"):
            out["int8_float_io"] = out["int8_float_io"] or p
        elif name.endswith("_float32.tflite"):
            out["float32"] = out["float32"] or p
    return out


def next_dev_version(exports: Path, base: str = "0.1.0") -> str:
    """``0.1.0-dev.N`` with N one higher than any existing bundle dir (bundles are immutable)."""
    n = 0
    if exports.is_dir():
        for d in exports.iterdir():
            if d.name.startswith(f"{base}-dev."):
                tail = d.name.rsplit(".", 1)[-1]
                if tail.isdigit():
                    n = max(n, int(tail))
    return f"{base}-dev.{n + 1}"


def sh(cmd: Sequence[str] | str, *, cwd: Path | None = None, env: Mapping[str, str] | None = None, check: bool = True,
       timeout: float | None = None) -> int:  # pragma: no cover - thin subprocess wrapper
    args = shlex.split(cmd) if isinstance(cmd, str) else [str(c) for c in cmd]
    print("$", " ".join(shlex.quote(a) for a in args), flush=True)
    t0 = time.time()
    res = subprocess.run(args, cwd=cwd, env=None if env is None else {**os.environ, **env}, check=False, timeout=timeout)
    print(f"[exit {res.returncode} in {time.time() - t0:.0f}s]", flush=True)
    if check and res.returncode != 0:
        raise RuntimeError(f"command failed ({res.returncode}): {args[0]} ...")
    return res.returncode


def ensure_venv(path: Path, python: str, packages: Sequence[str], *, extra_args: Sequence[str] = ()) -> Path:  # pragma: no cover
    """uv-managed venv (downloads a standalone CPython if the kernel lacks that version). Returns its python."""
    py = path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not py.exists():
        sh([sys.executable, "-m", "pip", "install", "-q", "uv==0.12.7"])
        sh([sys.executable, "-m", "uv", "venv", "--python", python, str(path)])
    sh([sys.executable, "-m", "uv", "pip", "install", "--python", str(py), *extra_args, *packages])
    return py


def gpu_summary() -> str:  # pragma: no cover - needs nvidia-smi
    try:
        return subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                              capture_output=True, text=True, check=False, timeout=30).stdout.strip() or "no GPU"
    except (OSError, subprocess.TimeoutExpired):
        return "no GPU (nvidia-smi missing)"


def env_record(extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {"compute_env": compute_env(), "python": sys.version.split()[0], "platform": sys.platform, "gpu": gpu_summary(),
            "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **(extra or {})}
