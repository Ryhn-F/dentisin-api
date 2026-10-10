from __future__ import annotations

from pathlib import Path

import pytest

from canonical import (
    CLASS_NAMES,
    AnnotationError,
    Box,
    ImageAnnotation,
    clip_box,
    read_jsonl,
    validate_box,
    validate_image,
    write_jsonl,
)


def test_class_names_are_v1_contract() -> None:
    assert CLASS_NAMES == ("caries_permanent", "caries_primary")


def test_box_geometry_and_roundtrip() -> None:
    b = Box("caries_permanent", 1, 2, 5, 10, score=0.5)
    assert (b.width, b.height, b.area) == (4, 8, 32)
    assert Box.from_dict(b.to_dict()) == b
    assert "score" not in Box("caries_permanent", 0, 0, 1, 1).to_dict()


def test_malformed_box_raises() -> None:
    with pytest.raises(AnnotationError):
        Box.from_dict({"cls": "x", "x1": "nope"})
    with pytest.raises(AnnotationError):
        ImageAnnotation.from_dict({"image": "a"})


@pytest.mark.parametrize(
    ("box", "expected"),
    [
        (Box("caries_permanent", 0, 0, 10, 10), []),
        (Box("tooth", 0, 0, 10, 10), ["unknown class"]),
        (Box("caries_permanent", -1, 0, 10, 10), ["out of bounds"]),
        (Box("caries_permanent", 0, 0, 11, 10), ["out of bounds"]),
        (Box("caries_permanent", 5, 5, 5.5, 9), ["degenerate"]),
        (Box("caries_permanent", 6, 5, 5, 9), ["degenerate"]),
        (Box("caries_permanent", float("nan"), 0, 1, 1), ["NaN"]),
        (Box("caries_permanent", 0, 0, 5, 5, score=1.5), ["score"]),
    ],
)
def test_validate_box(box: Box, expected: list[str]) -> None:
    problems = validate_box(box, 10, 10)
    assert len(problems) == len(expected)
    for p, e in zip(problems, expected, strict=True):
        assert e in p


def test_validate_image() -> None:
    assert validate_image(ImageAnnotation("a", 0, 10)) == ["a: non-positive image size 0x10"]
    bad = ImageAnnotation("a", 10, 10, [Box("caries_permanent", 0, 0, 20, 5)])
    assert "box[0]" in validate_image(bad)[0]


def test_clip_box() -> None:
    assert clip_box(Box("c", -0.5, -1, 10.4, 11, 0.2), 10, 10) == Box("c", 0, 0, 10, 10, 0.2)


def test_jsonl_roundtrip(tmp_path: Path) -> None:
    recs = [ImageAnnotation("x/a.jpg", 10, 8, [Box("caries_primary", 1, 1, 3, 3)], source="s", patient_id="p1"),
            ImageAnnotation("x/b.jpg", 10, 8)]
    path = tmp_path / "sub" / "c.jsonl"
    write_jsonl(recs, path)
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")  # blank lines tolerated
    assert read_jsonl(path) == recs


def test_jsonl_reports_line(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    path.write_text('{"image": "a", "width": 1, "height": 1}\nnot json\n', encoding="utf-8")
    with pytest.raises(AnnotationError, match=":2:"):
        read_jsonl(path)
