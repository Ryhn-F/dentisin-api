"""Trainer adapters, imported lazily by train.py.

Each adapter module exposes ``train(ctx: TrainContext) -> dict``. Heavy dependencies (TensorFlow,
PyTorch, MediaPipe Model Maker, YOLOX, rfdetr) are imported INSIDE ``train`` so that the base
install (numpy/pillow/pyyaml/jsonschema) can validate configs and write run records on any CPU.

The adapters are written against the libraries' documented APIs but were NOT executed in this
repository (no GPU, and several toolchains need older Python versions). Treat them as the
starting point for the first Kaggle/Colab run, not as tested code.
"""

from __future__ import annotations

import importlib
from types import ModuleType

from trainers.base import MissingOptionalDependencyError, TrainContext

ADAPTERS: dict[str, str] = {
    "mediapipe_model_maker": "trainers.mediapipe_mm",
    "yolox": "trainers.yolox_adapter",
    "efficientdet_lite": "trainers.efficientdet_lite",
    "rfdetr": "trainers.rfdetr_adapter",
}


def load_adapter(trainer: str) -> ModuleType:
    if trainer not in ADAPTERS:
        raise KeyError(f"unknown trainer {trainer!r}; known: {sorted(ADAPTERS)}")
    return importlib.import_module(ADAPTERS[trainer])


__all__ = ["ADAPTERS", "MissingOptionalDependencyError", "TrainContext", "load_adapter"]
