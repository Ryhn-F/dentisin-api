"""Reference PRE-processing (DECODE_SPEC.md §1) and representative/calibration sets.

    python preprocess.py calib --cfg configs/yolox_nano.yaml --split train --n 200 --out out/calib.npy
    python preprocess.py calib ... --gt ... --splits ... --images-root ...   # override config paths

:func:`preprocess` turns an 8-bit RGB photo into the model input tensor (float, NHWC, batch 1)
exactly as described in DECODE_SPEC §1 steps 1-3 (quantisation, step 4, is done by the runner
from the interpreter's input quantisation). The SAME function feeds INT8 calibration
(``calib``), INT8 parity samples and ``predict.py``, so calibration, evaluation and the app all
see one preprocessing definition.

Calibration samples come from the TRAIN split only (never val/test), drawn deterministically
with ``seed``. The output ``.npy`` holds preprocessed float32 tensors -- not photos -- but it is
still derived from images: keep it out of downloads/artifacts (``artifacts.py`` refuses it).

Known limitation: resizing uses Pillow bilinear, which anti-aliases when downscaling; YOLOX
training uses OpenCV ``INTER_LINEAR`` (no anti-aliasing) and the app uses its own resizer.
Small numeric differences are expected and are measured, not assumed away (spike S1).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from canonical import ImageAnnotation, read_jsonl
from decode import Letterbox, letterbox_params

FloatArr = npt.NDArray[np.float32]
RESAMPLE = {"bilinear": "BILINEAR", "nearest": "NEAREST", "area": "BOX"}


@dataclass(frozen=True)
class InputSpec:
    """Model input description; built from a config's ``model`` block or a labels.json ``input`` block."""

    width: int
    height: int
    color_order: str
    pixel_range: tuple[float, float]
    mean: tuple[float, float, float]
    std: tuple[float, float, float]
    resize_method: str
    align: str
    pad_value: int | None
    interpolation: str

    @staticmethod
    def from_config(cfg: Mapping[str, Any]) -> InputSpec:
        m = cfg["model"]
        pre = m["preprocess"]
        w, h = m["input_size"]
        return InputSpec(int(w), int(h), pre["color_order"], _pair(pre["pixel_range"]), _triple(pre["mean"]),
                         _triple(pre["std"]), pre["resize_method"], pre["align"], pre["pad_value"], pre["interpolation"])

    @staticmethod
    def from_labels(labels: Mapping[str, Any]) -> InputSpec:
        i = labels["input"]
        n = i["normalization"]
        r = i["resize"]
        return InputSpec(int(i["width"]), int(i["height"]), i["color_order"], _pair(n["pixel_range"]), _triple(n["mean"]),
                         _triple(n["std"]), r["method"], r["align"], r["pad_value"], r["interpolation"])


def _pair(v: Sequence[float]) -> tuple[float, float]:
    return float(v[0]), float(v[1])


def _triple(v: Sequence[float]) -> tuple[float, float, float]:
    return float(v[0]), float(v[1]), float(v[2])


@dataclass(frozen=True)
class Geometry:
    """How the photo was placed into the input (needed to map boxes back, DECODE_SPEC §5)."""

    src_w: int
    src_h: int
    method: str
    letterbox: Letterbox | None


def _resize(img: Any, size: tuple[int, int], interpolation: str) -> Any:
    from PIL import Image

    return img.resize(size, resample=getattr(Image.Resampling, RESAMPLE[interpolation]))


def preprocess(rgb: npt.NDArray[np.uint8], spec: InputSpec) -> tuple[FloatArr, Geometry]:
    """8-bit RGB (H, W, 3) -> float32 tensor (1, Hin, Win, 3) + geometry. DECODE_SPEC §1 steps 1-3, 5."""
    from PIL import Image

    arr = np.asarray(rgb)
    if arr.ndim != 3 or arr.shape[2] != 3 or arr.dtype != np.uint8:
        raise ValueError(f"expected uint8 RGB (H, W, 3), got {arr.dtype} {arr.shape}")
    src_h, src_w = int(arr.shape[0]), int(arr.shape[1])
    img = Image.fromarray(arr)
    lb: Letterbox | None = None
    if spec.resize_method == "stretch":
        canvas = np.asarray(_resize(img, (spec.width, spec.height), spec.interpolation), dtype=np.uint8)
    elif spec.resize_method == "letterbox":
        lb = letterbox_params(src_w, src_h, spec.width, spec.height, spec.align)
        new_w, new_h = int(src_w * lb.scale), int(src_h * lb.scale)  # floor, as YOLOX preproc
        resized = np.asarray(_resize(img, (max(new_w, 1), max(new_h, 1)), spec.interpolation), dtype=np.uint8)
        pad = 0 if spec.pad_value is None else int(spec.pad_value)
        canvas = np.full((spec.height, spec.width, 3), pad, dtype=np.uint8)
        ox, oy = round(lb.pad_x), round(lb.pad_y)
        # pixel placement needs integer offsets; mapping back keeps the continuous pad of DECODE_SPEC §5
        canvas[oy:oy + resized.shape[0], ox:ox + resized.shape[1]] = resized
    else:
        raise ValueError(f"unknown resize method {spec.resize_method!r}")
    if spec.color_order == "BGR":
        canvas = canvas[..., ::-1]
    elif spec.color_order != "RGB":
        raise ValueError(f"unknown color order {spec.color_order!r}")
    lo, hi = spec.pixel_range
    v = lo + canvas.astype(np.float32) * np.float32((hi - lo) / 255.0)
    x = (v - np.asarray(spec.mean, dtype=np.float32)) / np.asarray(spec.std, dtype=np.float32)
    return x[None, ...].astype(np.float32), Geometry(src_w, src_h, spec.resize_method, lb)


def load_rgb(path: Path) -> npt.NDArray[np.uint8]:
    """Decode WITHOUT applying EXIF orientation: canonical width/height (convert_annotations.pil_size)
    are the stored pixel dimensions, so boxes live in that frame. Whether the dataset annotators saw
    EXIF-rotated images is [PERLU VERIFIKASI] (verify_dataset should report EXIF orientation != 1).
    Training copies are written without EXIF (convert_annotations.resized_copy) so OpenCV-based
    trainers (YOLOX, which honours EXIF) see the same frame."""
    from PIL import Image

    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"), dtype=np.uint8)


def check_size(arr: npt.NDArray[np.uint8], rec: ImageAnnotation) -> None:
    h, w = int(arr.shape[0]), int(arr.shape[1])
    if (w, h) != (rec.width, rec.height):
        raise ValueError(f"{rec.image}: decoded {w}x{h} != annotated {rec.width}x{rec.height} (EXIF rotation or wrong file?)")


def split_records(gt: Path, splits: Path, split: str) -> list[ImageAnnotation]:
    assign = json.loads(splits.read_text(encoding="utf-8"))["images"]
    recs = [r for r in read_jsonl(gt) if assign.get(r.image) == split]
    if not recs:
        raise ValueError(f"no images in split {split!r}")
    return recs


def pick(records: Sequence[ImageAnnotation], n: int, seed: int) -> list[ImageAnnotation]:
    """Deterministic, platform-independent sample: order by sha256(seed:image), take n."""
    keyed = sorted(records, key=lambda r: hashlib.sha256(f"{seed}:{r.image}".encode()).hexdigest())
    return keyed[: max(0, n)]


def build_calibration(records: Sequence[ImageAnnotation], images_root: Path, spec: InputSpec) -> FloatArr:
    xs = []
    for r in records:
        arr = load_rgb(images_root / r.image)
        check_size(arr, r)
        xs.append(preprocess(arr, spec)[0][0])
    if not xs:
        raise ValueError("no calibration images")
    return np.stack(xs).astype(np.float32)


def main(argv: list[str] | None = None) -> int:
    from train import HERE, ConfigError, load_config, resolve

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("calib", help="representative dataset (.npy, NHWC float32) for INT8 calibration / parity")
    c.add_argument("--cfg", type=Path, required=True)
    c.add_argument("--split", default="train", choices=["train", "val"], help="train for calibration; val for parity samples")
    c.add_argument("--n", type=int, default=None, help="default: export.representative_samples from the config")
    c.add_argument("--seed", type=int, default=None)
    c.add_argument("--gt", type=Path)
    c.add_argument("--splits", type=Path)
    c.add_argument("--images-root", type=Path)
    c.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    try:
        cfg, _ = load_config(args.cfg)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    gt = args.gt or resolve(HERE, cfg["data"]["canonical"])
    splits = args.splits or resolve(HERE, cfg["data"]["splits"])
    root = args.images_root or resolve(HERE, cfg["data"].get("images_root"))
    if gt is None or splits is None or root is None:
        print("error: need --gt, --splits and --images-root (or data.* in the config)", file=sys.stderr)
        return 2
    n = int(args.n if args.n is not None else cfg["export"].get("representative_samples", 200))
    seed = int(args.seed if args.seed is not None else cfg["train"]["seed"])
    try:
        chosen = pick(split_records(gt, splits, args.split), n, seed)
        arr = build_calibration(chosen, root, InputSpec.from_config(cfg))
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.save(args.out, arr)
    meta = {"out": str(args.out), "shape": list(arr.shape), "split": args.split, "seed": seed, "n": len(chosen),
            "images_sha256": hashlib.sha256("\n".join(r.image for r in chosen).encode()).hexdigest()}
    args.out.with_suffix(".json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(meta, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
