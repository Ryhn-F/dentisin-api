"""ahmed2025_groups.py on SYNTHETIC filenames/images that mimic the verified Ahmed 2025 naming scheme."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path

import ahmed2025_groups as ag
import pytest
from PIL import Image

T0 = 1_728_000_000_000  # ms epoch


def main_name(subset: str, rec: str, rr: str, ts: int, view: str = "Frontal", sep: str = "-") -> str:
    return f"Dataset/Images/{subset}/{view}/anonymous_{rec.replace('-', sep)}{sep}{rr}_{ts}_{view}_View.jpg"


def pilot_name(view: str, ts: int, folder: str = "Frontal") -> str:
    return f"Dataset/Images/pilot/{folder}/anonymous-{view}View-{ts}.jpg"


def jpeg(model: str | None = None) -> bytes:
    buf = io.BytesIO()
    im = Image.new("RGB", (8, 6), (100, 100, 100))
    if model:
        ex = Image.Exif()
        ex[272] = model
        im.save(buf, format="JPEG", exif=ex.tobytes())
    else:
        im.save(buf, format="JPEG")
    return buf.getvalue()


def test_parse_names() -> None:
    i = ag.parse_image_name(main_name("retractors", "003-007-12", "01", T0, "Left_Lateral"))
    assert (i.kind, i.subset, i.view, i.record_id, i.ts_ms) == ("main", "retractors", "Left_Lateral", "003-007-12", T0)
    j = ag.parse_image_name(main_name("no_retractors", "003-007-13", "00", T0, sep="_"))
    assert j.record_id == "003-007-13"
    assert j.subset == "no_retractors"
    k = ag.parse_image_name(pilot_name("leftLateral", T0, "Left_Lateral"))
    assert (k.kind, k.view, k.record_id) == ("pilot", "leftLateral", "")
    with pytest.raises(ag.GroupError, match="known Ahmed"):
        ag.parse_image_name("Dataset/Images/pilot/Frontal/IMG_0001.jpg")
    with pytest.raises(ag.GroupError, match="unexpected"):
        ag.parse_image_name("x.jpg")


def _infos(specs: list[tuple[str, str, int]]) -> list[ag.ImageInfo]:
    out = []
    for rec, subset, ts in specs:
        rr = "01" if subset == "retractors" else "00"
        for view in ("Frontal", "Mandibular"):
            out.append(ag.parse_image_name(main_name(subset, rec, rr, ts, view)))
    return out


def test_group_main_rules() -> None:
    s = 1000
    infos = _infos([
        ("003-007-1", "retractors", T0),
        ("003-007-2", "no_retractors", T0 + 1500 * s),   # W/R -> W/O-R within pair_gap (1800 s): same child
        ("003-007-3", "retractors", T0 + 4000 * s),      # 2500 s later: new child
        ("003-007-4", "retractors", T0 + 4300 * s),      # 300 s <= link_gap: merged (conservative)
        ("003-007-5", "no_retractors", T0 + 9000 * s),   # 4700 s: new child
        ("003-007-6", "retractors", T0 + 10000 * s),     # W/O-R -> W/R 1000 s: NOT paired (wrong order, > link_gap)
    ])
    g = ag.group_main(infos)
    by_rec = {i.record_id: g[i.image] for i in infos}
    assert by_rec["003-007-1"] == by_rec["003-007-2"] == "main:003-007-1"
    assert by_rec["003-007-3"] == by_rec["003-007-4"] == "main:003-007-3"
    assert by_rec["003-007-5"] != by_rec["003-007-6"]
    # all views of one record share a group
    assert len({g[i.image] for i in infos if i.record_id == "003-007-1"}) == 1


def test_group_main_rejects_inconsistent_record() -> None:
    bad = [ag.parse_image_name(main_name("retractors", "003-007-1", "01", T0)),
           ag.parse_image_name(main_name("retractors", "003-007-1", "01", T0 + 5, "Mandibular"))]
    with pytest.raises(ag.GroupError, match="several timestamps"):
        ag.group_main(bad)


def test_group_pilot_per_device_and_link_to_main() -> None:
    s = 1000
    p = [ag.parse_image_name(pilot_name(v, T0 + k * 60 * s)) for k, v in enumerate(("frontal", "rightLateral", "leftLateral"))]
    later = ag.parse_image_name(pilot_name("frontal", T0 + 5000 * s))
    other_dev = ag.parse_image_name(pilot_name("maxillary", T0 + 30 * s, "Maxillary_Occlusal"))
    devices = {i.image: "A" for i in [*p, later]} | {other_dev.image: "B"}
    g = ag.group_pilot([*p, later, other_dev], devices)
    assert len({g[i.image] for i in p}) == 1
    assert g[later.image] != g[p[0].image]
    assert g[other_dev.image].startswith("pilot:B:")
    # unknown device -> "unknown" timeline
    assert ag.group_pilot([later], {})[later.image].startswith("pilot:unknown:")
    # pilot image 100 s after a main record -> groups merged; far ones stay separate
    main = _infos([("003-103-9", "no_retractors", T0 - 100 * s)])
    groups = {**ag.group_main(main), **g}
    linked = ag.link_pilot_to_main([*p, later, other_dev], main, groups)
    assert linked[p[0].image] == linked[main[0].image] == linked[other_dev.image]
    assert linked[later.image] == g[later.image]
    assert ag.link_pilot_to_main(p, [], g) == g  # no main records -> unchanged


def _dataset_files() -> dict[str, bytes]:
    s = 1000
    files = {
        main_name("retractors", "003-007-1", "01", T0): jpeg(),
        main_name("retractors", "003-007-1", "01", T0, "Mandibular"): jpeg(),
        main_name("no_retractors", "003-007-2", "00", T0 + 600 * s, "Mandibular", sep="_"): jpeg(),
        main_name("retractors", "003-007-3", "01", T0 + 9000 * s): jpeg(),
        pilot_name("frontal", T0 - 90_000 * s): jpeg("SM-A235F"),
        pilot_name("rightLateral", T0 - 89_950 * s, "Right_Lateral"): jpeg("SM-A235F"),
        pilot_name("frontal", T0 - 50_000 * s): jpeg(),
        "Dataset/Annotations/Darknet_YOLO/retractors/Mandibular/"
        f"anonymous_003-007-1-01_{T0}_Mandibular_View.txt": b"0 0.5 0.5 0.1 0.1\n1 0.2 0.2 0.1 0.1\n",
        "Dataset/Annotations/Darknet_YOLO/no_retractors/Mandibular/"
        f"anonymous_003_007_2_00_{T0 + 600 * s}_Mandibular_View.txt": b"0 0.5 1.7 0.1 0.1\n",
        "Dataset/Annotations/Darknet_YOLO/pilot/Mandibular/anonymous-123_Mandibular_View.txt": b"0 0.5 0.5 0.1 0.1\n",
    }
    return files


def _check_outputs(out: Path) -> None:
    rows = list(csv.DictReader((out / "groups.csv").open(encoding="utf-8", newline="")))
    assert len(rows) == 7
    by_img = {r["image"]: r for r in rows}
    rec1 = [r for r in rows if r["record_id"] == "003-007-1"]
    assert {r["patient_id"] for r in rec1} == {"main:003-007-1"}
    assert by_img[next(k for k in by_img if "003_007_2" in k)]["patient_id"] == "main:003-007-1"
    assert by_img[next(k for k in by_img if "003-007-1" in k and "Mandibular" in k)]["n_labels"] == "2"
    assert by_img[next(k for k in by_img if "003_007_2" in k)]["label_defect"] == "1"
    devices = {r["device"] for r in rows if r["subset"] == "pilot"}
    assert devices == {"SM-A235F", "unknown"}
    inputs = [json.loads(x) for x in (out / "split_input.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sum(1 for r in inputs if r["boxes"]) == 1
    assert [b["src"] for r in inputs for b in r["boxes"]] == ["D", "d"]
    summary = json.loads((out / "groups_summary.json").read_text(encoding="utf-8"))
    assert summary["images"] == 7
    assert summary["main_records"] == 3
    assert summary["images_with_label_defects"] == 1
    assert summary["orphan_label_files"] == ["anonymous-123_Mandibular_View"]
    assert summary["groups_main"] == 2
    assert summary["groups_pilot"] == 2


def test_cli_root_and_zip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    files = _dataset_files()
    root = tmp_path / "root"
    for name, data in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(data)
    assert ag.main(["--root", str(root), "--out-dir", str(tmp_path / "o1")]) == 0
    _check_outputs(tmp_path / "o1")
    zpath = tmp_path / "d.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    assert ag.main(["--zip", str(zpath), "--out-dir", str(tmp_path / "o2")]) == 0
    _check_outputs(tmp_path / "o2")
    assert (tmp_path / "o1" / "groups.csv").read_bytes() == (tmp_path / "o2" / "groups.csv").read_bytes()
    capsys.readouterr()
    assert ag.main(["--root", str(tmp_path / "missing"), "--out-dir", str(tmp_path / "o3")]) == 2
    assert "not found" in capsys.readouterr().err


def test_build_errors_and_root_without_labels(tmp_path: Path) -> None:
    with pytest.raises(ag.GroupError, match="no images"):
        ag.build(ag.Source([], lambda _: b"", {}))
    root = tmp_path / "r"
    p = root / pilot_name("frontal", T0)
    p.parent.mkdir(parents=True)
    p.write_bytes(jpeg())
    src = ag.Source.from_root(root)
    assert src.labels == {}
    rows, summary = ag.build(src, model_fn=lambda _: None)
    assert rows[0]["device"] == "unknown"
    assert summary["groups"] == 1
