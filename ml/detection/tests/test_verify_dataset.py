from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

import verify_dataset as vd


def build(root: Path, make_image: Callable[..., Path]) -> None:
    make_image(root / "Images" / "a" / "a" / "img1.jpg")
    make_image(root / "Images" / "a" / "img2.png")
    make_image(root / "Images" / "b" / "img3.jpg")
    (root / "Images" / "b" / "notes.txt").write_text("hello", encoding="utf-8")
    (root / "Images" / "b" / "fake.jpg").write_bytes(b"<html>error</html>")
    (root / "Images" / "b" / "Thumbs.db").write_bytes(b"x")
    # a PNG saved with a .jpg extension
    make_image(root / "tmp" / "x.png")
    (root / "Images" / "b" / "mislabeled.jpg").write_bytes((root / "tmp" / "x.png").read_bytes())
    lab = root / "Labels" / "a"
    lab.mkdir(parents=True)
    (lab / "img1.txt").write_text("0 0.5 0.5 0.2 0.2\n1 0.3 0.3 0.1 0.1\n", encoding="utf-8")
    (lab / "img2.txt").write_text("", encoding="utf-8")
    (lab / "classes.txt").write_text("caries\n", encoding="utf-8")
    (lab / "mislabeled.txt").write_text("0 0.5 0.5 0.1 0.1\n", encoding="utf-8")


def test_clean_counts(tmp_path: Path, make_image: Callable[..., Path]) -> None:
    build(tmp_path, make_image)
    rep = vd.verify(tmp_path / "Images", tmp_path / "Labels", num_classes=2, root=tmp_path).to_dict()
    assert rep["images"] == 4
    assert rep["label_files"] == 3
    assert rep["empty_label_files"] == 1
    assert rep["boxes"] == 3
    assert rep["class_histogram"] == {"0": 2, "1": 1}
    assert rep["images_without_label"]["examples"] == ["Images/b/img3.jpg"]
    assert set(rep["non_image_files"]["examples"]) == {"Images/b/notes.txt", "Images/b/fake.jpg"}
    assert rep["extension_mismatch"]["examples"] == ["Images/b/mislabeled.jpg is png"]
    assert rep["ok"] is True and len(rep["warnings"]) == 3


def test_errors(tmp_path: Path, make_image: Callable[..., Path]) -> None:
    build(tmp_path, make_image)
    (tmp_path / "Labels" / "a" / "orphan.txt").write_text("", encoding="utf-8")
    (tmp_path / "Labels" / "a" / "img1.txt").write_text(
        "0 0.5 0.5 0.2\n0 a b c d\n5 0.5 0.5 0.1 0.1\n0 1.5 0.5 0.1 0.1\n0 0.5 0.5 0 0.1\n0 0.95 0.5 0.2 0.1\n", encoding="utf-8")
    make_image(tmp_path / "Images" / "c" / "img2.jpg")  # duplicate stem
    rep = vd.verify(tmp_path / "Images", tmp_path / "Labels", num_classes=2, expected_images=99, root=tmp_path).to_dict()
    assert rep["ok"] is False
    assert rep["label_errors"]["count"] == 6
    msgs = " ".join(rep["label_errors"]["examples"])
    for m in ("expected 5 fields", "non-numeric", "outside [0, 2)", "outside [0, 1]", "degenerate", "beyond image"):
        assert m in msgs
    assert rep["duplicate_stems"]["examples"] == ["img2"]
    assert rep["labels_without_image"]["examples"] == ["Labels/a/orphan.txt"]
    assert any("expected 99" in e for e in rep["errors"])


def test_missing_dirs_and_empty(tmp_path: Path) -> None:
    rep = vd.verify(tmp_path / "nope", tmp_path / "nope2", 1)
    assert len(rep.errors) == 2
    (tmp_path / "i").mkdir()
    (tmp_path / "l").mkdir()
    assert "no images found" in vd.verify(tmp_path / "i", tmp_path / "l", 1).errors


@pytest.mark.parametrize(
    ("head", "kind"),
    [(b"\xff\xd8\xff\xe0", "jpeg"), (b"\x89PNG\r\n\x1a\n", "png"), (b"BM\x00", "bmp"), (b"RIFF\x00\x00\x00\x00WEBP", "webp"), (b"GIF8", None)],
)
def test_sniff(head: bytes, kind: str | None) -> None:
    assert vd.sniff_image(head) == kind


def test_cli(tmp_path: Path, make_image: Callable[..., Path], capsys: pytest.CaptureFixture[str]) -> None:
    build(tmp_path, make_image)
    out = tmp_path / "r" / "report.json"
    assert vd.main(["--images", str(tmp_path / "Images"), "--labels", str(tmp_path / "Labels"), "--num-classes", "2",
                    "--report", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["images"] == 4
    assert vd.main(["--images", str(tmp_path / "Images"), "--labels", str(tmp_path / "Labels"), "--num-classes", "2",
                    "--expected-images", "1"]) == 1
    assert vd.main(["--images", "x", "--labels", "y", "--num-classes", "0"]) == 2
    capsys.readouterr()
    rep = vd.verify(tmp_path / "Images", tmp_path / "Labels", 2)  # default root = images parent
    assert rep.images == 4
