"""verify_dataset.py zip front-end, orphan policy, class names, box-area histogram (SYNTHETIC data only)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image

import verify_dataset as vd


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 6), (90, 90, 90)).save(buf, format="JPEG")
    return buf.getvalue()


FILES: dict[str, bytes] = {
    "Dataset/Images/retractors/Frontal/a.jpg": _jpeg(),
    "Dataset/Images/pilot/Frontal/b.jpg": _jpeg(),
    "Dataset/Images/pilot/Frontal/c.jpg": _jpeg(),
    "Dataset/Images/pilot/Frontal/notes.txt": b"x",
    "Dataset/Annotations/Darknet_YOLO/retractors/Frontal/a.txt": b"0 0.5 0.5 0.2 0.2\n1 0.5 0.5 0.04 0.04\n",
    "Dataset/Annotations/Darknet_YOLO/pilot/Frontal/b.txt": b"",
    "Dataset/Annotations/Darknet_YOLO/pilot/Frontal/orphan.txt": b"0 0.5 0.5 0.9 0.9\n",
    "Dataset/Annotations/Darknet_YOLO/classes.txt": b"D\nd\n",
}


def make_zip(path: Path, files: dict[str, bytes] = FILES) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Dataset/", b"")  # directory entry is ignored
        for name, data in files.items():
            zf.writestr(name, data)
    return path


def make_tree(root: Path, files: dict[str, bytes] = FILES) -> Path:
    for name, data in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root


def test_zip_matches_directory(tmp_path: Path) -> None:
    zpath = make_zip(tmp_path / "d.zip")
    root = make_tree(tmp_path / "x")
    with zipfile.ZipFile(zpath) as zf:
        rz = vd.verify_zip(zf, "Dataset/Images", "Dataset/Annotations/Darknet_YOLO", 2, orphan_labels="warn",
                           class_names=["D", "d"]).to_dict()
    rd = vd.verify(root / "Dataset/Images", root / "Dataset/Annotations/Darknet_YOLO", 2, root=root, orphan_labels="warn",
                   class_names=["D", "d"]).to_dict()
    assert rz == rd
    assert rz["ok"] is True
    assert rz["images"] == 3
    assert rz["label_files"] == 3
    assert rz["empty_label_files"] == 1
    assert rz["class_histogram_by_name"] == {"D": 2, "d": 1}
    assert rz["box_area_histogram"] == {"<=0.0025": 1, "<=0.04": 1, "<=1.0": 1}
    assert rz["images_per_folder"] == {"Dataset/Images/pilot/Frontal": 2, "Dataset/Images/retractors/Frontal": 1}
    assert rz["labels_without_image"]["examples"] == ["Dataset/Annotations/Darknet_YOLO/pilot/Frontal/orphan.txt"]
    assert rz["images_without_label"]["examples"] == ["Dataset/Images/pilot/Frontal/c.jpg"]
    assert "1 label files without image" in rz["warnings"]


def test_orphans_are_errors_by_default(tmp_path: Path) -> None:
    with zipfile.ZipFile(make_zip(tmp_path / "d.zip")) as zf:
        rep = vd.verify_zip(zf, "Dataset/Images/", "Dataset/Annotations/Darknet_YOLO/", 2)
    assert rep.to_dict()["ok"] is False
    assert "1 label files without image" in rep.errors
    assert "class_histogram_by_name" not in rep.to_dict()


def test_zip_missing_prefix_and_bad_names(tmp_path: Path) -> None:
    with zipfile.ZipFile(make_zip(tmp_path / "d.zip")) as zf:
        rep = vd.verify_zip(zf, "Nope/Images", "Dataset/Annotations/Darknet_YOLO", 2)
        assert rep.errors == ["images prefix not found in archive: Nope/Images/"]
        rep = vd.verify_zip(zf, "Dataset/Images", "Dataset/Annotations/Darknet_YOLO", 2, class_names=["D"])
        assert "--class-names has 1 names" in rep.errors[0]


def test_area_bucket_edges() -> None:
    assert vd.area_bucket(0.04, 0.04) == "<=0.0025"
    assert vd.area_bucket(1.0, 1.0) == "<=1.0"
    assert vd.area_bucket(1.001, 1.001) == ">1.0"


def test_cli_zip_and_usage(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    zpath = make_zip(tmp_path / "d.zip")
    out = tmp_path / "r.json"
    assert vd.main(["--zip", str(zpath), "--num-classes", "2", "--class-names", "D,d", "--orphan-labels", "warn",
                    "--expected-images", "3", "--report", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["class_histogram_by_name"] == {"D": 2, "d": 1}
    assert vd.main(["--zip", str(zpath), "--num-classes", "2"]) == 1  # orphan -> error by default
    assert vd.main(["--num-classes", "2"]) == 2
    assert "give --images and --labels, or --zip" in capsys.readouterr().err
