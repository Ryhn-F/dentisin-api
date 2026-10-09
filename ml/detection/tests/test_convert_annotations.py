from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

import convert_annotations as ca
from canonical import AnnotationError, Box, ImageAnnotation, read_jsonl, write_jsonl

CMAP = {
    "status": "PLACEHOLDER",
    "yolo_names": ["cavity", "white_spot", "tooth"],
    "map": {"cavity": "caries_cavitated", "white_spot": "caries_noncavitated", "tooth": "__drop__"},
}


def cmap(**over: Any) -> ca.ClassMap:
    d = json.loads(json.dumps(CMAP))
    d.update(over)
    return ca.ClassMap.from_dict(d)


def test_classmap_resolution(tmp_path: Path) -> None:
    p = tmp_path / "m.yaml"
    p.write_text(yaml.safe_dump(CMAP), encoding="utf-8")
    m = ca.ClassMap.load(p)
    assert m.resolve("cavity") == "caries_cavitated"
    assert m.resolve("tooth") is None
    with pytest.raises(AnnotationError, match="not in the class map"):
        m.resolve("plaque")
    undecided = cmap(map={"caries": None})
    assert undecided.unresolved() == ["caries"]
    with pytest.raises(AnnotationError, match="PLACEHOLDER"):
        undecided.resolve("caries")
    with pytest.raises(AnnotationError, match="not a canonical class"):
        cmap(map={"caries": "calculus"})
    with pytest.raises(AnnotationError, match="mapping"):
        ca.ClassMap.from_dict({"map": ["x"]})


def test_repo_class_map_is_placeholder_and_blocks() -> None:
    path = Path(__file__).resolve().parents[2] / "data" / "class_maps" / "ahmed2025.yaml"
    m = ca.ClassMap.load(path)
    assert m.status == "PLACEHOLDER"
    assert m.unresolved(), "ahmed2025 map must stay undecided until the real labels + drg review"


def _yolo_dataset(root: Path, make_image: Callable[..., Path]) -> None:
    make_image(root / "images" / "Frontal" / "Frontal" / "p1.jpg", w=100, h=50)
    make_image(root / "images" / "Frontal" / "Frontal" / "p2.jpg", w=100, h=50)
    make_image(root / "images" / "Lateral" / "p3.png", w=100, h=50)
    (root / "labels" / "Frontal").mkdir(parents=True)
    (root / "labels" / "Lateral").mkdir(parents=True)
    # p1: nested image dir differs from label dir -> pairing by unique stem
    (root / "labels" / "Frontal" / "p1.txt").write_text("0 0.5 0.5 0.2 0.4\n1 0.1 0.1 0.1 0.1\n2 0.5 0.5 0.1 0.1\n\n", encoding="utf-8")
    (root / "labels" / "Frontal" / "p2.txt").write_text("", encoding="utf-8")  # negative image
    (root / "labels" / "Lateral" / "p3.txt").write_text("0 0.995 0.5 0.02 0.2\n", encoding="utf-8")  # 0.1 px past edge -> clipped


def test_read_yolo(tmp_path: Path, make_image: Callable[..., Path]) -> None:
    _yolo_dataset(tmp_path, make_image)
    res = ca.read_yolo(tmp_path, tmp_path / "images", tmp_path / "labels", cmap(), source="synthetic")
    by = {r.image: r for r in res.records}
    assert set(by) == {"images/Frontal/Frontal/p1.jpg", "images/Frontal/Frontal/p2.jpg", "images/Lateral/p3.png"}
    p1 = by["images/Frontal/Frontal/p1.jpg"]
    assert (p1.width, p1.height) == (100, 50)
    assert p1.boxes[0] == Box("caries_cavitated", 40.0, 15.0, 60.0, 35.0)
    assert p1.boxes[1].cls == "caries_noncavitated"
    assert pytest.approx(p1.boxes[1].x1) == 5.0
    assert res.dropped_boxes == 1  # 'tooth' dropped
    assert by["images/Frontal/Frontal/p2.jpg"].boxes == []
    assert by["images/Lateral/p3.png"].boxes[0].x2 == 100.0
    assert res.clipped_boxes == 1
    assert p1.source == "synthetic"


def test_read_yolo_errors(tmp_path: Path, make_image: Callable[..., Path]) -> None:
    make_image(tmp_path / "images" / "a.jpg")
    (tmp_path / "labels").mkdir()
    with pytest.raises(AnnotationError, match="no label file"):
        ca.read_yolo(tmp_path, tmp_path / "images", tmp_path / "labels", cmap(), source="s")
    res = ca.read_yolo(tmp_path, tmp_path / "images", tmp_path / "labels", cmap(), source="s", missing_label="negative")
    assert res.records[0].boxes == [] and "negative" in res.problems[0]
    with pytest.raises(AnnotationError, match="yolo_names"):
        ca.read_yolo(tmp_path, tmp_path / "images", tmp_path / "labels", cmap(yolo_names=[]), source="s")
    lab = tmp_path / "labels" / "a.txt"
    for content, msg in [("0 0.5 0.5 0.2\n", "expected 5"), ("x 0.5 0.5 0.2 0.2\n", "invalid literal"),
                         ("7 0.5 0.5 0.2 0.2\n", "not in yolo_names"), ("0 0.5 0.5 0.0 0.2\n", "degenerate"),
                         ("0 0.9 0.5 0.5 0.2\n", "out of bounds")]:
        lab.write_text(content, encoding="utf-8")
        with pytest.raises(AnnotationError, match=msg):
            ca.read_yolo(tmp_path, tmp_path / "images", tmp_path / "labels", cmap(), source="s")
    res = ca.read_yolo(tmp_path, tmp_path / "images", tmp_path / "labels", cmap(), source="s", on_invalid="drop")
    assert res.dropped_boxes == 1 and res.problems


def test_ambiguous_stem(tmp_path: Path, make_image: Callable[..., Path]) -> None:
    make_image(tmp_path / "images" / "x" / "a.jpg")
    for d in ("u", "v"):
        (tmp_path / "labels" / d).mkdir(parents=True)
        (tmp_path / "labels" / d / "a.txt").write_text("", encoding="utf-8")
    with pytest.raises(AnnotationError, match="ambiguous"):
        ca.read_yolo(tmp_path, tmp_path / "images", tmp_path / "labels", cmap(), source="s")


def test_read_coco(tmp_path: Path) -> None:
    coco = {
        "images": [{"id": 1, "file_name": "b.jpg", "width": 40, "height": 20}, {"id": 2, "file_name": "a.jpg", "width": 40, "height": 20}],
        "categories": [{"id": 3, "name": "cavity"}, {"id": 4, "name": "tooth"}],
        "annotations": [
            {"id": 1, "image_id": 1, "category_id": 3, "bbox": [2, 3, 10, 5]},
            {"id": 2, "image_id": 1, "category_id": 4, "bbox": [2, 3, 10, 5]},
        ],
    }
    p = tmp_path / "ann.json"
    p.write_text(json.dumps(coco), encoding="utf-8")
    res = ca.read_coco(tmp_path, p, cmap(), source="s", images_prefix="imgs")
    assert [r.image for r in res.records] == ["imgs/a.jpg", "imgs/b.jpg"]
    assert res.records[0].boxes == []
    assert res.records[1].boxes == [Box("caries_cavitated", 2, 3, 12, 8)]
    assert res.dropped_boxes == 1
    assert ca.read_coco(tmp_path, p, cmap(), source="s").records[0].image == "a.jpg"
    coco["annotations"].append({"id": 3, "image_id": 1, "category_id": 99, "bbox": [0, 0, 1, 1]})
    p.write_text(json.dumps(coco), encoding="utf-8")
    with pytest.raises(AnnotationError, match="unknown category"):
        ca.read_coco(tmp_path, p, cmap(), source="s")


VOC = """<annotation><filename>{name}</filename>{size}
<object><name>cavity</name><bndbox><xmin>1</xmin><ymin>2</ymin><xmax>8</xmax><ymax>9</ymax></bndbox></object>
<object><name>tooth</name><bndbox><xmin>1</xmin><ymin>2</ymin><xmax>8</xmax><ymax>9</ymax></bndbox></object>
</annotation>"""


def test_read_voc(tmp_path: Path, make_image: Callable[..., Path]) -> None:
    make_image(tmp_path / "img" / "a.jpg", w=20, h=10)
    make_image(tmp_path / "img" / "b.jpg", w=20, h=10)
    ann = tmp_path / "ann"
    ann.mkdir()
    (ann / "a.xml").write_text(VOC.format(name="a.jpg", size="<size><width>20</width><height>10</height></size>"), encoding="utf-8")
    (ann / "b.xml").write_text(VOC.format(name="b.jpg", size=""), encoding="utf-8")  # size from image header
    res = ca.read_voc(tmp_path, ann, tmp_path / "img", cmap(), source="s")
    assert [r.image for r in res.records] == ["img/a.jpg", "img/b.jpg"]
    assert all(r.boxes == [Box("caries_cavitated", 1, 2, 8, 9)] for r in res.records)
    assert res.dropped_boxes == 2
    (ann / "c.xml").write_text(VOC.format(name="missing.jpg", size=""), encoding="utf-8")
    with pytest.raises(AnnotationError, match="matched 0 files"):
        ca.read_voc(tmp_path, ann, tmp_path / "img", cmap(), source="s")


def test_voc_rejects_entity_expansion(tmp_path: Path, make_image: Callable[..., Path]) -> None:
    make_image(tmp_path / "img" / "a.jpg")
    ann = tmp_path / "ann"
    ann.mkdir()
    (ann / "a.xml").write_text('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><annotation>&a;</annotation>', encoding="utf-8")
    from defusedxml import EntitiesForbidden

    with pytest.raises(EntitiesForbidden):
        ca.read_voc(tmp_path, ann, tmp_path / "img", cmap(), source="s")


def _records() -> list[ImageAnnotation]:
    return [
        ImageAnnotation("d/x/a.jpg", 20, 10, [Box("caries_cavitated", 1, 1, 5, 6), Box("caries_noncavitated", 6, 1, 9, 4)]),
        ImageAnnotation("d/y/a.jpg", 20, 10, []),
    ]


def test_coco_dict() -> None:
    d = ca.to_coco_dict(_records())
    assert [c["id"] for c in d["categories"]] == [1, 2]
    assert [i["file_name"] for i in d["images"]] == ["d__x__a.jpg", "d__y__a.jpg"]  # flattened, no collision
    assert d["annotations"][0] == {"id": 1, "image_id": 1, "category_id": 1, "bbox": [1, 1, 4, 5], "area": 20, "iscrowd": 0}
    assert d["annotations"][1]["category_id"] == 2


@pytest.mark.parametrize(
    ("layout", "split", "ann", "imgdir"),
    [
        ("mediapipe", "train", "train/labels.json", "train/data"),
        ("yolox", "val", "annotations/instances_val.json", "val"),
        ("roboflow", "val", "valid/_annotations.coco.json", "valid"),
    ],
)
def test_layouts(tmp_path: Path, make_image: Callable[..., Path], layout: Any, split: str, ann: str, imgdir: str) -> None:
    root = tmp_path / "src"
    make_image(root / "d" / "x" / "a.jpg")
    make_image(root / "d" / "y" / "a.jpg")
    out = tmp_path / "out"
    path = ca.write_coco_layout(_records(), out, layout=layout, split=split, images_root=root)
    assert path == out / ann
    assert sorted(p.name for p in (out / imgdir).iterdir() if p.suffix == ".jpg") == ["d__x__a.jpg", "d__y__a.jpg"]
    assert len(json.loads(path.read_text(encoding="utf-8"))["annotations"]) == 2
    assert (root / "d" / "x" / "a.jpg").exists()  # copied, never moved


def test_bad_layout(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown layout"):
        ca.write_coco_layout([], tmp_path, layout="voc", split="train")  # type: ignore[arg-type]


def test_voc_writer_roundtrip(tmp_path: Path, make_image: Callable[..., Path]) -> None:
    root = tmp_path / "src"
    make_image(root / "d" / "x" / "a.jpg")
    make_image(root / "d" / "y" / "a.jpg")
    ca.write_voc_layout(_records(), tmp_path / "voc", split="train", images_root=root)
    back = ca.read_voc(tmp_path / "voc", tmp_path / "voc" / "train" / "Annotations", tmp_path / "voc" / "train" / "images",
                       ca.ClassMap.from_dict({"map": {"caries_cavitated": "caries_cavitated", "caries_noncavitated": "caries_noncavitated"}}),
                       source="s")
    assert back.records[0].boxes == _records()[0].boxes


def test_partition() -> None:
    parts = ca.partition(_records(), {"d/x/a.jpg": "train", "d/y/a.jpg": "test"})
    assert {k: len(v) for k, v in parts.items()} == {"train": 1, "test": 1}
    with pytest.raises(AnnotationError, match="no split"):
        ca.partition(_records(), {})


def test_cli_end_to_end(tmp_path: Path, make_image: Callable[..., Path], capsys: pytest.CaptureFixture[str]) -> None:
    _yolo_dataset(tmp_path / "ds", make_image)
    m = tmp_path / "map.yaml"
    m.write_text(yaml.safe_dump(CMAP), encoding="utf-8")
    out = tmp_path / "canon.jsonl"
    assert ca.main(["to-canonical", "--in-format", "yolo", "--root", str(tmp_path / "ds"), "--images", "images", "--labels", "labels",
                    "--class-map", str(m), "--source", "synthetic", "--out", str(out)]) == 0
    assert json.loads(capsys.readouterr().out)["images"] == 3
    recs = read_jsonl(out)
    splits = tmp_path / "splits.json"
    splits.write_text(json.dumps({"images": {r.image: ("train" if i else "val") for i, r in enumerate(recs)}}), encoding="utf-8")
    for layout in ("mediapipe", "voc"):
        assert ca.main(["export", "--canonical", str(out), "--splits", str(splits), "--layout", layout,
                        "--images-root", str(tmp_path / "ds"), "--copy-images", "--out", str(tmp_path / layout)]) == 0
    assert (tmp_path / "mediapipe" / "val" / "labels.json").is_file()
    assert (tmp_path / "voc" / "train" / "Annotations").is_dir()
    # coco + voc CLI paths
    coco = tmp_path / "ds" / "c.json"
    coco.write_text(json.dumps(ca.to_coco_dict(recs)), encoding="utf-8")
    cm2 = tmp_path / "map2.yaml"
    cm2.write_text(yaml.safe_dump({"map": {"caries_cavitated": "caries_cavitated", "caries_noncavitated": "caries_noncavitated"}}), encoding="utf-8")
    assert ca.main(["to-canonical", "--in-format", "coco", "--root", str(tmp_path / "ds"), "--coco-json", "c.json",
                    "--class-map", str(cm2), "--source", "s", "--out", str(tmp_path / "c2.jsonl")]) == 0
    write_jsonl(recs, tmp_path / "unused.jsonl")
    assert ca.main(["to-canonical", "--in-format", "voc", "--root", str(tmp_path / "voc" / "train"), "--images", "images",
                    "--labels", "Annotations", "--class-map", str(cm2), "--source", "s", "--out", str(tmp_path / "c3.jsonl")]) == 0


def test_cli_refuses_placeholder_map(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    m = tmp_path / "map.yaml"
    m.write_text(yaml.safe_dump({"map": {"caries": None}}), encoding="utf-8")
    rc = ca.main(["to-canonical", "--in-format", "yolo", "--root", str(tmp_path), "--images", "i", "--labels", "l",
                  "--class-map", str(m), "--source", "s", "--out", str(tmp_path / "o.jsonl")])
    assert rc == 2
    assert "undecided" in capsys.readouterr().err
