"""MediaPipe Model Maker object detector adapter (MobileNetV2 SSD/RetinaNet, QAT INT8).

Model Maker 0.2.1.4 (last release 2024-04-22, docs: "no longer actively maintained") needs
Python <= 3.10 and TensorFlow < 2.16, while this pipeline needs Python >= 3.11. The adapter
therefore runs in the pipeline's Python, writes the COCO layout, and launches
``kaggle/mm_train_py310.py`` in a SEPARATE Python 3.10 environment (``trainer_options.python``),
which trains, runs QAT, exports ``model_fp32.tflite`` + ``model_int8_qat.tflite`` and writes the
anchors (``anchors.json``, [yc, xc, h, w] normalised -> ``export.py --anchors``).

UNTESTED HERE (no Python 3.10/TF 2.15 env, no GPU). Pure helpers are unit-tested.

Licences (WEIGHT_LICENSES.md): only ``MOBILENET_V2`` (i256) is verified -- its checkpoint is the one
listed by the Apache-2.0 TF Model Garden QAT project; MULTI_AVG checkpoints are not listed there.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from trainers.base import MissingOptionalDependencyError, TrainContext

HERE = Path(__file__).resolve().parent.parent
SCRIPT = HERE / "kaggle" / "mm_train_py310.py"
INSTALL_HINT = ("a Python 3.10 env: uv venv --python 3.10 /kaggle/tmp/venv-mm && "
                "uv pip install --python /kaggle/tmp/venv-mm mediapipe-model-maker==0.2.1.4 'tensorflow[and-cuda]==2.15.1'")
VERIFIED_VARIANTS = ("MOBILENET_V2",)


def options(ctx: TrainContext) -> dict[str, Any]:
    opts: dict[str, Any] = dict(ctx.cfg.get("trainer_options", {}))
    opts.setdefault("work_dir", str(ctx.run_dir / "work"))
    opts.setdefault("max_side", 640)
    opts.setdefault("cache_dir", str(Path(str(opts["work_dir"])) / "mm_cache"))
    return opts


def build_command(ctx: TrainContext, opts: Mapping[str, Any], layout_dir: Path, export_dir: Path) -> list[str]:
    python = opts.get("python")
    if not python:
        raise MissingOptionalDependencyError(f"trainer_options.python (Python 3.10 interpreter) not set; create it with: {INSTALL_HINT}")
    t = ctx.cfg["train"]
    return [str(python), str(SCRIPT), "--train", str(layout_dir / "train"), "--val", str(layout_dir / "val"),
            "--export-dir", str(export_dir), "--variant", str(ctx.cfg["model"]["variant"]), "--epochs", str(t["epochs"]),
            "--batch-size", str(t["batch_size"]), "--learning-rate", str(t["learning_rate"]), "--qat-epochs", str(t.get("qat_epochs", 0)),
            "--qat-learning-rate", str(opts.get("qat_learning_rate", t["learning_rate"])), "--cache-dir", str(opts["cache_dir"]),
            "--seed", str(ctx.seed)]


def train(ctx: TrainContext) -> dict[str, Any]:  # pragma: no cover - needs a Python 3.10 Model Maker env + GPU
    from canonical import read_jsonl
    from convert_annotations import load_split_assignments, partition, resized_copy, write_coco_layout

    variant = ctx.cfg["model"]["variant"]
    if variant not in VERIFIED_VARIANTS:
        raise MissingOptionalDependencyError(f"variant {variant!r}: pretrained weight licence not verified; use one of {VERIFIED_VARIANTS}")
    if ctx.images_root is None:
        raise MissingOptionalDependencyError("data.images_root is required for training")
    opts = options(ctx)
    work = Path(str(opts["work_dir"]))
    layout_dir = work / "data_mediapipe"
    parts = partition(read_jsonl(ctx.canonical), load_split_assignments(ctx.splits))
    for split in ("train", "val"):
        small = resized_copy(parts[split], ctx.images_root, work / "images", max_side=int(opts["max_side"]))
        write_coco_layout(small, layout_dir, layout="mediapipe", split=split, images_root=work / "images", link=True)
    export_dir = ctx.run_dir / "exported"
    cmd = build_command(ctx, opts, layout_dir, export_dir)
    subprocess.run(cmd, check=True, timeout=None if opts.get("time_budget_h") is None else float(opts["time_budget_h"]) * 3600)
    result: dict[str, Any] = json.loads((export_dir / "mm_result.json").read_text(encoding="utf-8"))
    result["command"] = cmd
    result["note"] = "Model Maker COCO metrics are NOT the DentisAI report; run predict.py + eval.py on the exported INT8 model."
    return result
