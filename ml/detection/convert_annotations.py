"""Convert YOLO / COCO / Pascal VOC annotations into the canonical format, and write
trainer-specific layouts (MediaPipe Model Maker COCO, YOLOX COCO, Roboflow/RF-DETR COCO, VOC).

Class mapping is ALWAYS explicit (``--class-map``): a source class name maps to a canonical
class, to ``__drop__``, or is left ``null`` (= undecided -> conversion refuses to run). For Ahmed 2025
(ADR 014) the map is ``D: caries_permanent``, ``d: caries_primary`` (straight through, by NAME -- the COCO files use
different category ids per file); nothing is inferred about severity or cavitation.

Usage::

    python convert_annotations.py to-canonical --in-format yolo --root <dataset_root> \
        --images Dataset/Images --labels Dataset/Annotations/Darknet_YOLO \
        --class-map ../data/class_maps/ahmed2025.yaml --source ahmed2025 --out canonical.jsonl
    python convert_annotations.py export --canonical canonical.jsonl --splits splits.json \
        --layout mediapipe --images-root <dataset_root> --out build/mp_coco --copy-images
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import defusedxml.ElementTree as SafeET
import yaml

from canonical import (
    CLASS_NAMES,
    AnnotationError,
    Box,
    ImageAnnotation,
    clip_box,
    read_jsonl,
    validate_box,
    write_jsonl,
)

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
DROP = "__drop__"
#: Boxes that exceed the image by at most this many pixels are clipped (float rounding in
#: exported annotations); anything larger is an error.
CLIP_TOLERANCE_PX = 1.0

OnInvalid = Literal["error", "drop"]
Layout = Literal["mediapipe", "yolox", "roboflow"]
SizeFn = Callable[[Path], tuple[int, int]]


@dataclass
class ClassMap:
    """Explicit source-name -> canonical-name mapping loaded from YAML."""

    mapping: dict[str, str | None]
    yolo_names: list[str] = field(default_factory=list)
    status: str = "PLACEHOLDER"

    @staticmethod
    def load(path: Path) -> ClassMap:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return ClassMap.from_dict(data)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> ClassMap:
        mapping = data.get("map") or {}
        if not isinstance(mapping, dict):
            raise AnnotationError("class map: 'map' must be a mapping")
        for src, dst in mapping.items():
            if dst is not None and dst != DROP and dst not in CLASS_NAMES:
                raise AnnotationError(f"class map: {src!r} -> {dst!r} is not a canonical class")
        names = data.get("yolo_names") or []
        return ClassMap(
            mapping={str(k): v for k, v in mapping.items()},
            yolo_names=[str(n) for n in names],
            status=str(data.get("status", "PLACEHOLDER")),
        )

    def unresolved(self) -> list[str]:
        return sorted(k for k, v in self.mapping.items() if v is None)

    def resolve(self, source_name: str) -> str | None:
        """Return canonical name, or None if the box must be dropped. Raises if unmapped/undecided."""
        if source_name not in self.mapping:
            raise AnnotationError(f"source class {source_name!r} is not in the class map")
        dst = self.mapping[source_name]
        if dst is None:
            raise AnnotationError(f"source class {source_name!r} is mapped to null (undecided, PLACEHOLDER)")
        return None if dst == DROP else dst


@dataclass
class ConversionResult:
    records: list[ImageAnnotation]
    problems: list[str] = field(default_factory=list)
    dropped_boxes: int = 0
    clipped_boxes: int = 0


def pil_size(path: Path) -> tuple[int, int]:
    """Image (width, height) from the header only (no full decode)."""
    from PIL import Image

    with Image.open(path) as im:
        return int(im.width), int(im.height)


def _finalize_box(
    raw: Box, width: int, height: int, origin: str, result: ConversionResult, on_invalid: OnInvalid
) -> Box | None:
    box = raw
    if (
        raw.x1 >= -CLIP_TOLERANCE_PX
        and raw.y1 >= -CLIP_TOLERANCE_PX
        and raw.x2 <= width + CLIP_TOLERANCE_PX
        and raw.y2 <= height + CLIP_TOLERANCE_PX
    ):
        clipped = clip_box(raw, width, height)
        if clipped != raw:
            result.clipped_boxes += 1
        box = clipped
    problems = validate_box(box, width, height)
    if problems:
        msg = f"{origin}: " + "; ".join(problems)
        if on_invalid == "error":
            raise AnnotationError(msg)
        result.problems.append(msg)
        result.dropped_boxes += 1
        return None
    return box


def _rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def list_images(images_dir: Path) -> list[Path]:
    return sorted(p for p in images_dir.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS)


def _label_index(labels_dir: Path, suffix: str) -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = {}
    for p in sorted(labels_dir.rglob(f"*{suffix}")):
        index.setdefault(p.stem, []).append(p)
    return index


def find_label(image: Path, images_dir: Path, labels_dir: Path, index: dict[str, list[Path]], suffix: str) -> Path | None:
    """Pair an image with its label file: same relative path first, then a unique stem match."""
    direct = labels_dir / image.relative_to(images_dir).with_suffix(suffix)
    if direct.is_file():
        return direct
    candidates = index.get(image.stem, [])
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise AnnotationError(f"ambiguous label for {image}: {[str(c) for c in candidates]}")
    return None


def read_yolo(
    root: Path,
    images_dir: Path,
    labels_dir: Path,
    class_map: ClassMap,
    *,
    source: str,
    missing_label: Literal["error", "negative"] = "error",
    on_invalid: OnInvalid = "error",
    size_fn: SizeFn = pil_size,
) -> ConversionResult:
    """YOLO txt: ``<class_id> <cx> <cy> <w> <h>`` normalised to [0, 1]."""
    if not class_map.yolo_names:
        raise AnnotationError("YOLO input needs 'yolo_names' (id -> source name) in the class map")
    result = ConversionResult(records=[])
    index = _label_index(labels_dir, ".txt")
    for image in list_images(images_dir):
        width, height = size_fn(image)
        label = find_label(image, images_dir, labels_dir, index, ".txt")
        boxes: list[Box] = []
        if label is None:
            if missing_label == "error":
                raise AnnotationError(f"no label file for {image}")
            result.problems.append(f"{image}: no label file, treated as negative")
        else:
            for lineno, line in enumerate(label.read_text(encoding="utf-8").splitlines(), start=1):
                parts = line.split()
                if not parts:
                    continue
                origin = f"{label}:{lineno}"
                if len(parts) != 5:
                    raise AnnotationError(f"{origin}: expected 5 fields, got {len(parts)} (segmentation labels unsupported)")
                try:
                    cid = int(parts[0])
                    cx, cy, w, h = (float(v) for v in parts[1:])
                except ValueError as exc:
                    raise AnnotationError(f"{origin}: {exc}") from exc
                if not 0 <= cid < len(class_map.yolo_names):
                    raise AnnotationError(f"{origin}: class id {cid} not in yolo_names")
                name = class_map.resolve(class_map.yolo_names[cid])
                if name is None:
                    result.dropped_boxes += 1
                    continue
                raw = Box(
                    name,
                    (cx - w / 2) * width,
                    (cy - h / 2) * height,
                    (cx + w / 2) * width,
                    (cy + h / 2) * height,
                )
                box = _finalize_box(raw, width, height, origin, result, on_invalid)
                if box is not None:
                    boxes.append(box)
        result.records.append(ImageAnnotation(_rel(image, root), width, height, boxes, source=source))
    return result


def read_coco(
    root: Path,
    coco_json: Path,
    class_map: ClassMap,
    *,
    source: str,
    images_prefix: str = "",
    on_invalid: OnInvalid = "error",
) -> ConversionResult:
    """COCO detection JSON. ``bbox`` = [x, y, w, h] in pixels. Images without annotations = negatives."""
    data = json.loads(coco_json.read_text(encoding="utf-8"))
    cats = {int(c["id"]): str(c["name"]) for c in data.get("categories", [])}
    by_image: dict[int, list[dict[str, Any]]] = {}
    for ann in data.get("annotations", []):
        by_image.setdefault(int(ann["image_id"]), []).append(ann)
    result = ConversionResult(records=[])
    for img in sorted(data.get("images", []), key=lambda i: str(i["file_name"])):
        width, height = int(img["width"]), int(img["height"])
        rel = (Path(images_prefix) / str(img["file_name"])).as_posix() if images_prefix else str(img["file_name"])
        boxes: list[Box] = []
        for ann in by_image.get(int(img["id"]), []):
            cid = int(ann["category_id"])
            if cid not in cats:
                raise AnnotationError(f"{coco_json}: annotation {ann.get('id')} has unknown category {cid}")
            name = class_map.resolve(cats[cid])
            if name is None:
                result.dropped_boxes += 1
                continue
            x, y, w, h = (float(v) for v in ann["bbox"])
            box = _finalize_box(Box(name, x, y, x + w, y + h), width, height, f"{coco_json}:ann{ann.get('id')}", result, on_invalid)
            if box is not None:
                boxes.append(box)
        result.records.append(ImageAnnotation(rel, width, height, boxes, source=source))
    _ = root  # root kept for signature symmetry; COCO paths are relative to images_prefix
    return result


def read_voc(
    root: Path,
    annotations_dir: Path,
    images_dir: Path,
    class_map: ClassMap,
    *,
    source: str,
    on_invalid: OnInvalid = "error",
    size_fn: SizeFn = pil_size,
) -> ConversionResult:
    """Pascal VOC XML (parsed with defusedxml). Coordinates are taken as pixel coordinates as-is
    (no 1-based offset correction; document if a source is known to be 1-based)."""
    result = ConversionResult(records=[])
    image_index: dict[str, list[Path]] = {}
    for p in list_images(images_dir):
        image_index.setdefault(p.name, []).append(p)
    for xml_path in sorted(annotations_dir.rglob("*.xml")):
        tree = SafeET.parse(xml_path)
        node = tree.getroot()
        if node is None:
            raise AnnotationError(f"{xml_path}: empty XML document")
        filename = (node.findtext("filename") or "").strip()
        matches = image_index.get(filename, [])
        if len(matches) != 1:
            raise AnnotationError(f"{xml_path}: image {filename!r} matched {len(matches)} files")
        image = matches[0]
        size = node.find("size")
        if size is not None and size.findtext("width") and size.findtext("height"):
            width, height = int(float(size.findtext("width") or 0)), int(float(size.findtext("height") or 0))
        else:
            width, height = size_fn(image)
        if width <= 0 or height <= 0:
            width, height = size_fn(image)
        boxes: list[Box] = []
        for i, obj in enumerate(node.findall("object")):
            name = class_map.resolve((obj.findtext("name") or "").strip())
            if name is None:
                result.dropped_boxes += 1
                continue
            bb = obj.find("bndbox")
            if bb is None:
                raise AnnotationError(f"{xml_path}: object {i} has no bndbox")
            try:
                coords = [float(bb.findtext(k) or "nan") for k in ("xmin", "ymin", "xmax", "ymax")]
            except ValueError as exc:
                raise AnnotationError(f"{xml_path}: object {i}: {exc}") from exc
            box = _finalize_box(Box(name, *coords), width, height, f"{xml_path}:obj{i}", result, on_invalid)
            if box is not None:
                boxes.append(box)
        result.records.append(ImageAnnotation(_rel(image, root), width, height, boxes, source=source))
    return result


# --------------------------------------------------------------------------------------- writers


def flat_name(rel_image: str) -> str:
    """Unique flat file name for nested source paths (``a/b/c.jpg`` -> ``a__b__c.jpg``)."""
    return rel_image.replace("\\", "/").strip("/").replace("/", "__")


def to_coco_dict(records: Iterable[ImageAnnotation]) -> dict[str, Any]:
    """COCO dict with category ids starting at 1 (0 is reserved for background by MediaPipe
    Model Maker; YOLOX/RF-DETR remap sorted category ids to contiguous indices internally)."""
    categories = [{"id": i + 1, "name": n, "supercategory": "caries"} for i, n in enumerate(CLASS_NAMES)]
    cat_id = {n: i + 1 for i, n in enumerate(CLASS_NAMES)}
    images: list[dict[str, Any]] = []
    annotations: list[dict[str, Any]] = []
    ann_id = 1
    for img_id, rec in enumerate(records, start=1):
        images.append({"id": img_id, "file_name": flat_name(rec.image), "width": rec.width, "height": rec.height})
        for box in rec.boxes:
            w, h = box.x2 - box.x1, box.y2 - box.y1
            annotations.append(
                {
                    "id": ann_id,
                    "image_id": img_id,
                    "category_id": cat_id[box.cls],
                    "bbox": [box.x1, box.y1, w, h],
                    "area": w * h,
                    "iscrowd": 0,
                }
            )
            ann_id += 1
    # "info"/"licenses": pycocotools 2.0.9/2.0.10 loadRes() raise KeyError 'info' without them, and a kernel that already
    # has an importable pycocotools (Colab ships its own) is not reinstalled by nbkit.ensure_kernel_modules
    return {"info": {"description": "DentisAI detection training copy"}, "licenses": [], "images": images,
            "annotations": annotations, "categories": categories}


def _layout_paths(out_dir: Path, layout: Layout, split: str) -> tuple[Path, Path]:
    """Return (annotation_json_path, image_dir) for a layout and split."""
    if layout == "mediapipe":
        # mediapipe-model-maker 0.2.1.4 Dataset.from_coco_folder(<split>/): "<data_dir>/images/<file>.jpg" +
        # "<data_dir>/labels.json" (read from the wheel's docstring; the old TFLite Model Maker used data/).
        return out_dir / split / "labels.json", out_dir / split / "images"
    if layout == "yolox":  # COCODataset(data_dir, json_file=instances_<split>.json, name=<split>)
        return out_dir / "annotations" / f"instances_{split}.json", out_dir / split
    if layout == "roboflow":  # rfdetr: <split>/_annotations.coco.json; uses 'valid' for val
        name = "valid" if split == "val" else split
        return out_dir / name / "_annotations.coco.json", out_dir / name
    raise ValueError(f"unknown layout {layout!r}")


def write_coco_layout(
    records: list[ImageAnnotation],
    out_dir: Path,
    *,
    layout: Layout,
    split: str,
    images_root: Path | None = None,
    link: bool = False,
) -> Path:
    """Write one split. If ``images_root`` is given, images are copied (never moved) into the layout,
    or symlinked when ``link`` (falls back to copying where symlinks are not permitted)."""
    ann_path, img_dir = _layout_paths(out_dir, layout, split)
    ann_path.parent.mkdir(parents=True, exist_ok=True)
    img_dir.mkdir(parents=True, exist_ok=True)
    ann_path.write_text(json.dumps(to_coco_dict(records), indent=1), encoding="utf-8")
    if images_root is not None:
        for rec in records:
            src, dst = images_root / rec.image, img_dir / flat_name(rec.image)
            if dst.exists() or dst.is_symlink():
                dst.unlink()
            if link:
                try:
                    dst.symlink_to(src.resolve())
                    continue
                except OSError:  # e.g. Windows without developer mode
                    pass
            shutil.copy2(src, dst)
    return ann_path


def resized_copy(
    records: list[ImageAnnotation],
    images_root: Path,
    out_dir: Path,
    *,
    max_side: int,
    quality: int = 95,
) -> list[ImageAnnotation]:
    """Write training copies (longest side <= ``max_side``, JPEG, NO EXIF) under ``out_dir`` and
    return records with scaled boxes/sizes (same relative ``image`` paths, now rooted at ``out_dir``).

    Why: (1) hosted-notebook dataloaders decode 12 MP JPEGs slowly; (2) stripping EXIF makes
    OpenCV-based trainers (which honour EXIF orientation) see the same pixel frame as the
    annotations. Pixels are decoded WITHOUT EXIF rotation; a decoded size that differs from the
    annotated size is an error. Evaluation always uses the ORIGINAL photos (predict.py).
    ``out_dir`` must be scratch space that is never exported (e.g. /kaggle/tmp)."""
    from PIL import Image

    out: list[ImageAnnotation] = []
    for rec in records:
        dst = out_dir / rec.image
        with Image.open(images_root / rec.image) as im:
            if (im.width, im.height) != (rec.width, rec.height):
                raise AnnotationError(f"{rec.image}: file is {im.width}x{im.height}, annotation says {rec.width}x{rec.height}")
            r = min(1.0, max_side / max(rec.width, rec.height))
            w, h = max(1, round(rec.width * r)), max(1, round(rec.height * r))
            if not dst.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                img = im.convert("RGB")
                img = img.resize((w, h), resample=Image.Resampling.BILINEAR) if r < 1.0 else img
                img.save(dst.with_suffix(".jpg") if dst.suffix.lower() not in (".jpg", ".jpeg") else dst, "JPEG", quality=quality)
        sx, sy = w / rec.width, h / rec.height
        boxes = [Box(b.cls, b.x1 * sx, b.y1 * sy, min(b.x2 * sx, float(w)), min(b.y2 * sy, float(h))) for b in rec.boxes]
        image = rec.image if Path(rec.image).suffix.lower() in (".jpg", ".jpeg") else str(Path(rec.image).with_suffix(".jpg").as_posix())
        out.append(ImageAnnotation(image=image, width=w, height=h, boxes=boxes, source=rec.source, patient_id=rec.patient_id))
    return out


def write_voc_layout(records: list[ImageAnnotation], out_dir: Path, *, split: str, images_root: Path | None = None) -> Path:
    """Pascal VOC (``<split>/images`` + ``<split>/Annotations``) for tflite-model-maker
    ``DataLoader.from_pascal_voc`` (EfficientDet-Lite path)."""
    from xml.sax.saxutils import escape

    ann_dir = out_dir / split / "Annotations"
    img_dir = out_dir / split / "images"
    ann_dir.mkdir(parents=True, exist_ok=True)
    img_dir.mkdir(parents=True, exist_ok=True)
    for rec in records:
        name = flat_name(rec.image)
        objs = "".join(
            f"<object><name>{escape(b.cls)}</name><difficult>0</difficult><bndbox>"
            f"<xmin>{b.x1:.2f}</xmin><ymin>{b.y1:.2f}</ymin><xmax>{b.x2:.2f}</xmax><ymax>{b.y2:.2f}</ymax>"
            "</bndbox></object>"
            for b in rec.boxes
        )
        xml = (
            f"<annotation><filename>{escape(name)}</filename><size><width>{rec.width}</width>"
            f"<height>{rec.height}</height><depth>3</depth></size>{objs}</annotation>"
        )
        (ann_dir / (Path(name).stem + ".xml")).write_text(xml, encoding="utf-8")
        if images_root is not None:
            shutil.copy2(images_root / rec.image, img_dir / name)
    return ann_dir


def load_split_assignments(splits_path: Path) -> dict[str, str]:
    """``splits.json`` (from make_splits.py) -> {image: split}."""
    data = json.loads(splits_path.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in data["images"].items()}


def partition(records: list[ImageAnnotation], assignments: dict[str, str]) -> dict[str, list[ImageAnnotation]]:
    out: dict[str, list[ImageAnnotation]] = {}
    for rec in records:
        if rec.image not in assignments:
            raise AnnotationError(f"{rec.image} has no split assignment")
        out.setdefault(assignments[rec.image], []).append(rec)
    return out


# ------------------------------------------------------------------------------------------- CLI


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("to-canonical")
    c.add_argument("--in-format", choices=["yolo", "coco", "voc"], required=True)
    c.add_argument("--root", type=Path, required=True, help="dataset root; canonical image paths are relative to it")
    c.add_argument("--images", type=Path, help="images dir (relative to root)")
    c.add_argument("--labels", type=Path, help="YOLO labels dir / VOC Annotations dir (relative to root)")
    c.add_argument("--coco-json", type=Path)
    c.add_argument("--images-prefix", default="", help="COCO: prefix joined to file_name")
    c.add_argument("--class-map", type=Path, required=True)
    c.add_argument("--source", required=True, help="dataset id from ml/data/license_gate.yaml")
    c.add_argument("--missing-label", choices=["error", "negative"], default="error")
    c.add_argument("--on-invalid", choices=["error", "drop"], default="error")
    c.add_argument("--out", type=Path, required=True)
    e = sub.add_parser("export")
    e.add_argument("--canonical", type=Path, required=True)
    e.add_argument("--splits", type=Path, required=True)
    e.add_argument("--layout", choices=["mediapipe", "yolox", "roboflow", "voc"], required=True)
    e.add_argument("--images-root", type=Path)
    e.add_argument("--copy-images", action="store_true")
    e.add_argument("--link-images", action="store_true", help="symlink instead of copying (Linux notebooks)")
    e.add_argument("--resize-max-side", type=int, help="write EXIF-free training copies (longest side <= N) first")
    e.add_argument("--resized-dir", type=Path, help="scratch dir for --resize-max-side copies (never exported)")
    e.add_argument("--out", type=Path, required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.cmd == "to-canonical":
        cmap = ClassMap.load(args.class_map)
        if cmap.unresolved():
            print(f"class map has undecided (null) entries: {cmap.unresolved()} -- fill them first", file=sys.stderr)
            return 2
        root: Path = args.root
        if args.in_format == "yolo":
            res = read_yolo(root, root / args.images, root / args.labels, cmap, source=args.source,
                            missing_label=args.missing_label, on_invalid=args.on_invalid)
        elif args.in_format == "coco":
            res = read_coco(root, root / args.coco_json, cmap, source=args.source,
                            images_prefix=args.images_prefix, on_invalid=args.on_invalid)
        else:
            res = read_voc(root, root / args.labels, root / args.images, cmap, source=args.source, on_invalid=args.on_invalid)
        write_jsonl(res.records, args.out)
        summary = {"images": len(res.records), "boxes": sum(len(r.boxes) for r in res.records),
                   "dropped_boxes": res.dropped_boxes, "clipped_boxes": res.clipped_boxes,
                   "problems": len(res.problems), "class_map_status": cmap.status}
        print(json.dumps(summary, indent=2))
        return 0
    records = read_jsonl(args.canonical)
    parts = partition(records, load_split_assignments(args.splits))
    images_root = args.images_root if (args.copy_images or args.link_images) else None
    if args.resize_max_side:
        if args.images_root is None or args.resized_dir is None:
            print("--resize-max-side needs --images-root and --resized-dir", file=sys.stderr)
            return 2
        parts = {s: resized_copy(r, args.images_root, args.resized_dir, max_side=args.resize_max_side) for s, r in parts.items()}
        images_root = args.resized_dir
    for split, recs in sorted(parts.items()):
        if args.layout == "voc":
            write_voc_layout(recs, args.out, split=split, images_root=images_root)
        else:
            write_coco_layout(recs, args.out, layout=args.layout, split=split, images_root=images_root, link=args.link_images)
    print(json.dumps({s: len(r) for s, r in sorted(parts.items())}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
