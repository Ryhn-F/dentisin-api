"""EfficientDet-Lite adapter via tflite-model-maker (archived toolchain).

UNTESTED HERE. tflite-model-maker requires an old Python/TensorFlow stack; check before relying on it
and record the difficulty in the bake-off report ("export difficulty").
"""

from __future__ import annotations

from typing import Any

from trainers.base import TrainContext, require

INSTALL_HINT = "an old Python/TF env (e.g. Python 3.9): pip install tflite-model-maker  [PERLU VERIFIKASI versions]"


def train(ctx: TrainContext) -> dict[str, Any]:  # pragma: no cover - needs tflite-model-maker
    tmm = require("tflite_model_maker", INSTALL_HINT)
    from canonical import CLASS_NAMES, read_jsonl
    from convert_annotations import load_split_assignments, partition, write_voc_layout

    layout = ctx.run_dir / "data_voc"
    parts = partition(read_jsonl(ctx.canonical), load_split_assignments(ctx.splits))
    for split in ("train", "val"):
        write_voc_layout(parts[split], layout, split=split, images_root=ctx.images_root)
    label_map = {i + 1: n for i, n in enumerate(CLASS_NAMES)}
    od = tmm.object_detector
    train_data = od.DataLoader.from_pascal_voc(str(layout / "train" / "images"), str(layout / "train" / "Annotations"), label_map)
    val_data = od.DataLoader.from_pascal_voc(str(layout / "val" / "images"), str(layout / "val" / "Annotations"), label_map)
    spec = tmm.model_spec.get(ctx.cfg["model"]["variant"])
    t = ctx.cfg["train"]
    model = od.create(train_data, model_spec=spec, batch_size=t["batch_size"], epochs=t["epochs"],
                      train_whole_model=bool(ctx.cfg.get("trainer_options", {}).get("train_whole_model", True)),
                      validation_data=val_data)
    out = ctx.run_dir / "exported"
    model.export(export_dir=str(out), tflite_filename="model_int8.tflite")  # default export is full-integer
    return {"artifacts": [str(out / "model_int8.tflite")]}
