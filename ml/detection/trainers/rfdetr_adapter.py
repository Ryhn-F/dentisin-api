"""RF-DETR Nano adapter (LOW PRIORITY). UNTESTED HERE.

Only Apache-2.0 sizes may be used (README: Nano/Small/Medium/Large); PML-1.0 sizes are excluded.
"""

from __future__ import annotations

from typing import Any

from trainers.base import MissingOptionalDependencyError, TrainContext, require

INSTALL_HINT = "uv sync --extra rfdetr"
ALLOWED_VARIANTS = ("RFDETRNano", "RFDETRSmall")


def train(ctx: TrainContext) -> dict[str, Any]:  # pragma: no cover - needs rfdetr + torch
    variant = ctx.cfg["model"]["variant"]
    if variant not in ALLOWED_VARIANTS:
        raise MissingOptionalDependencyError(f"variant {variant!r} not allowed (license); use one of {ALLOWED_VARIANTS}")
    rfdetr = require("rfdetr", INSTALL_HINT)
    from canonical import read_jsonl
    from convert_annotations import load_split_assignments, partition, write_coco_layout

    data_dir = ctx.run_dir / "data_roboflow"
    parts = partition(read_jsonl(ctx.canonical), load_split_assignments(ctx.splits))
    for split in ("train", "val", "test"):
        write_coco_layout(parts.get(split, []), data_dir, layout="roboflow", split=split, images_root=ctx.images_root)
    t = ctx.cfg["train"]
    model = getattr(rfdetr, variant)()
    model.train(dataset_dir=str(data_dir), epochs=t["epochs"], batch_size=t["batch_size"], lr=t["learning_rate"],
                output_dir=str(ctx.run_dir / "rfdetr_out"))
    return {"output_dir": str(ctx.run_dir / "rfdetr_out")}
