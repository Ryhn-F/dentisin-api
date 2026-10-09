"""MediaPipe Model Maker object detector adapter (MobileNetV2 SSD family).

UNTESTED HERE. Written against the documented API
(https://developers.google.com/edge/mediapipe/solutions/customization/object_detector, read
2026-10-08): Dataset.from_coco_folder, SupportedModels, HParams, ObjectDetectorOptions,
ObjectDetector.create, evaluate, quantization_aware_training(QATHParams), export_model.
Requires a Python 3.10 environment (package classifiers stop at 3.10).
"""

from __future__ import annotations

from typing import Any

from trainers.base import TrainContext, require

INSTALL_HINT = "a Python 3.10 env (Colab/Kaggle): pip install mediapipe-model-maker==0.2.1.4"


def train(ctx: TrainContext) -> dict[str, Any]:  # pragma: no cover - needs mediapipe-model-maker
    mm = require("mediapipe_model_maker", INSTALL_HINT)
    from canonical import read_jsonl
    from convert_annotations import load_split_assignments, partition, write_coco_layout

    od = mm.object_detector
    layout_dir = ctx.run_dir / "data_mediapipe"
    parts = partition(read_jsonl(ctx.canonical), load_split_assignments(ctx.splits))
    for split in ("train", "val"):
        write_coco_layout(parts[split], layout_dir, layout="mediapipe", split=split, images_root=ctx.images_root)
    cache = str(ctx.cfg.get("trainer_options", {}).get("cache_dir", ctx.run_dir / "cache"))
    train_data = od.Dataset.from_coco_folder(str(layout_dir / "train"), cache_dir=cache + "/train")
    val_data = od.Dataset.from_coco_folder(str(layout_dir / "val"), cache_dir=cache + "/val")
    t = ctx.cfg["train"]
    hparams = od.HParams(
        export_dir=str(ctx.run_dir / "exported"),
        epochs=t["epochs"],
        batch_size=t["batch_size"],
        learning_rate=t["learning_rate"],
    )
    options = od.ObjectDetectorOptions(supported_model=getattr(od.SupportedModels, ctx.cfg["model"]["variant"]), hparams=hparams)
    model = od.ObjectDetector.create(train_data=train_data, validation_data=val_data, options=options)
    loss, coco_metrics = model.evaluate(val_data, batch_size=t["batch_size"])
    model.export_model("model_fp32.tflite")
    result: dict[str, Any] = {"val_loss": loss, "val_coco_metrics_model_maker": coco_metrics,
                              "artifacts": [str(ctx.run_dir / "exported" / "model_fp32.tflite")]}
    if ctx.cfg["export"]["int8"] and t.get("qat_epochs", 0) > 0:
        qat = od.QATHParams(learning_rate=t["learning_rate"], batch_size=t["batch_size"], epochs=t["qat_epochs"])
        model.quantization_aware_training(train_data, validation_data=val_data, qat_hparams=qat)
        model.export_model("model_int8_qat.tflite")
        result["artifacts"].append(str(ctx.run_dir / "exported" / "model_int8_qat.tflite"))
    result["note"] = "Model Maker COCO metrics are NOT the DentisAI report; run eval.py on test predictions."
    return result
