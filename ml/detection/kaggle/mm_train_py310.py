"""MediaPipe Model Maker training worker -- runs INSIDE a Python 3.10 env (TF 2.15, Model Maker 0.2.1.4).

Launched by ``trainers/mediapipe_mm.py``; stdlib + mediapipe_model_maker only (do NOT import the
pipeline modules here: they need Python >= 3.11).

    /kaggle/tmp/venv-mm/bin/python mm_train_py310.py --train <coco>/train --val <coco>/val \
        --export-dir <run>/exported --variant MOBILENET_V2 --epochs 30 --batch-size 8 \
        --learning-rate 0.3 --qat-epochs 10 --qat-learning-rate 0.3 --cache-dir /kaggle/tmp/mm_cache

Writes to --export-dir: model_fp32.tflite, model_int8_qat.tflite (if --qat-epochs > 0),
metadata.json (Model Maker), anchors.json ([yc, xc, h, w] normalised, same order as the raw
output rows), mm_result.json. UNTESTED in the repo (no TF 2.15 env); API per Model Maker 0.2.1.4
source (object_detector.py export_model: raw boxes+scores, LOCATION_SCORE order, anchors in
metadata, uint8 I/O after QAT).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path
from typing import Any


def anchors_from_model(model: Any) -> list[list[float]]:
    """Same computation as ObjectDetector._create_fixed_anchor (Model Maker 0.2.1.4), same order."""
    h, w = model._model_spec.input_image_shape[0], model._model_spec.input_image_shape[1]
    out: list[list[float]] = []
    for _, boxes in model._preprocessor.anchor_boxes.items():
        for y1, x1, y2, x2 in boxes.numpy().reshape((-1, 4)).tolist():
            out.append([(y1 + y2) / (2 * h), (x1 + x2) / (2 * w), (y2 - y1) / h, (x2 - x1) / w])
    return out


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - needs mediapipe_model_maker
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--train", type=Path, required=True)
    p.add_argument("--val", type=Path, required=True)
    p.add_argument("--export-dir", type=Path, required=True)
    p.add_argument("--variant", default="MOBILENET_V2")
    p.add_argument("--epochs", type=int, default=30)
    p.add_argument("--batch-size", type=int, default=8)
    p.add_argument("--learning-rate", type=float, default=0.3)
    p.add_argument("--qat-epochs", type=int, default=10)
    p.add_argument("--qat-learning-rate", type=float, default=0.3)
    p.add_argument("--cache-dir", type=Path, required=True)
    p.add_argument("--seed", type=int, default=20261008)
    args = p.parse_args(argv)
    if sys.version_info[:2] > (3, 10):  # noqa: UP036 - this worker runs on Python 3.10
        print(f"warning: Model Maker 0.2.1.4 declares Python <= 3.10; running {sys.version.split()[0]}", file=sys.stderr)
    os.environ.setdefault("PYTHONHASHSEED", str(args.seed))
    random.seed(args.seed)
    import tensorflow as tf
    from mediapipe_model_maker import object_detector as od

    tf.keras.utils.set_random_seed(args.seed)
    train_data = od.Dataset.from_coco_folder(str(args.train), cache_dir=str(args.cache_dir / "train"))
    val_data = od.Dataset.from_coco_folder(str(args.val), cache_dir=str(args.cache_dir / "val"))
    hparams = od.HParams(export_dir=str(args.export_dir), epochs=args.epochs, batch_size=args.batch_size,
                         learning_rate=args.learning_rate)
    options = od.ObjectDetectorOptions(supported_model=getattr(od.SupportedModels, args.variant), hparams=hparams)
    model = od.ObjectDetector.create(train_data=train_data, validation_data=val_data, options=options)
    loss, coco = model.evaluate(val_data, batch_size=args.batch_size)
    model.export_model("model_fp32.tflite")
    args.export_dir.mkdir(parents=True, exist_ok=True)
    (args.export_dir / "anchors.json").write_text(json.dumps(anchors_from_model(model)) + "\n", encoding="utf-8")
    result: dict[str, Any] = {"variant": args.variant, "val_loss_float": [float(x) for x in loss] if isinstance(loss, list | tuple) else float(loss),
                              "val_coco_float_model_maker": {k: float(v) for k, v in dict(coco).items()},
                              "label_names": list(getattr(train_data, "label_names", [])),
                              "artifacts": ["model_fp32.tflite", "anchors.json", "metadata.json"],
                              "python": sys.version.split()[0], "tensorflow": tf.__version__}
    if args.qat_epochs > 0:
        qat = od.QATHParams(learning_rate=args.qat_learning_rate, batch_size=args.batch_size, epochs=args.qat_epochs)
        model.quantization_aware_training(train_data, validation_data=val_data, qat_hparams=qat)
        _qloss, qcoco = model.evaluate(val_data, batch_size=args.batch_size)
        model.export_model("model_int8_qat.tflite")
        result["val_coco_qat_model_maker"] = {k: float(v) for k, v in dict(qcoco).items()}
        result["artifacts"].append("model_int8_qat.tflite")
    (args.export_dir / "mm_result.json").write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
