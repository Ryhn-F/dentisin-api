"""split_stats.py + make_splits.py ``session_csv`` mode on SYNTHETIC groups."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
import split_stats as ss

import make_splits as ms


def write_inputs(tmp: Path, n_groups: int = 30) -> tuple[Path, Path]:
    canon = tmp / "split_input.jsonl"
    groups = tmp / "groups.csv"
    with canon.open("w", encoding="utf-8") as fc, groups.open("w", encoding="utf-8", newline="") as fg:
        w = csv.writer(fg)
        w.writerow(["image", "patient_id", "subset", "view", "record_id", "ts_ms", "device", "n_labels", "label_defect"])
        for g in range(n_groups):
            for v in range(3):
                img = f"Dataset/Images/retractors/V{v}/r{g}_{v}.jpg"
                boxes = [{"src": "D"}] if g % 3 == 0 and v == 0 else []
                fc.write(json.dumps({"image": img, "boxes": boxes}) + "\n")
                w.writerow([img, f"main:{g}", "retractors" if g % 2 else "pilot", f"V{v}", f"rec{g}", 0,
                            "" if g % 2 else "SM-X", len(boxes), 1 if g == 1 and v == 1 else 0])
    return canon, groups


def test_session_csv_mode_and_stats(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    canon, groups = write_inputs(tmp_path)
    out = tmp_path / "splits"
    assert ms.main(["--canonical", str(canon), "--out-dir", str(out), "--group-mode", "session_csv",
                    "--metadata-csv", str(groups)]) == 0
    man = json.loads((out / "split_manifest.json").read_text(encoding="utf-8"))
    assert man["group_quality"] == "heuristic_session"
    assert man["input_file"] == "split_input.jsonl"
    assert "filename timestamps" in man["limitations"][0]
    splits = json.loads((out / "splits.json").read_text(encoding="utf-8"))
    assert all(g.startswith("sess:main:") for g in splits["groups"])
    stats_out = tmp_path / "stats.json"
    assert ss.main(["--splits", str(out / "splits.json"), "--groups-csv", str(groups), "--split-input", str(canon),
                    "--out", str(stats_out)]) == 0
    res = json.loads(stats_out.read_text(encoding="utf-8"))
    assert res["leakage"]["groups_in_multiple_splits"] == 0
    assert res["leakage"]["records_in_multiple_splits"] == 0
    per = res["per_split"]
    assert sum(s["images"] for s in per.values()) == 90
    assert sum(s["images_with_boxes"] for s in per.values()) == 10
    assert sum(s["images_with_label_defects"] for s in per.values()) == 1
    assert all("by_subset" in s and "by_view" in s for s in per.values())
    assert all(sum(s["by_device"].values()) == s["images"] for s in per.values() if "by_device" in s)
    capsys.readouterr()


def test_leakage_detects_cross_split_pairs_and_records(tmp_path: Path) -> None:
    rows = {"a": {"patient_id": "g1", "record_id": "r1"}, "b": {"patient_id": "g1", "record_id": "r1"},
            "c": {"patient_id": "g2", "record_id": ""}}
    assign = {"a": "train", "b": "test", "c": "test", "z": "val"}
    lk = ss.leakage(assign, rows, [["a", "c"], ["b", "c"]])
    assert lk["groups_in_multiple_splits"] == 1
    assert lk["records_in_multiple_splits"] == 1
    assert lk["cross_split_pairs"] == 1
    assert lk["cross_split_pair_examples"] == [["a", "c"]]
    assert lk["images_without_group_row"] == 1
    st = ss.per_split_stats(assign, rows, {"a": ["D"]})
    assert st["train"]["positive_fraction"] == 1.0
    assert "by_subset" not in st["train"]
    # CLI exit code 1 on leaks
    sp = tmp_path / "s.json"
    sp.write_text(json.dumps({"images": assign}), encoding="utf-8")
    gc = tmp_path / "g.csv"
    with gc.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["image", "patient_id", "record_id"])
        for k, r in rows.items():
            w.writerow([k, r["patient_id"], r["record_id"]])
    pairs = tmp_path / "p.json"
    pairs.write_text(json.dumps([["a", "c"]]), encoding="utf-8")
    assert ss.main(["--splits", str(sp), "--groups-csv", str(gc), "--pairs", str(pairs)]) == 1


def test_load_boxes_accepts_canonical(tmp_path: Path) -> None:
    p = tmp_path / "c.jsonl"
    p.write_text(json.dumps({"image": "a", "boxes": [{"cls": "caries_cavitated"}, {}]}) + "\n\n", encoding="utf-8")
    assert ss.load_boxes(p) == {"a": ["caries_cavitated", "?"]}


def test_ahmed_class_map_follows_adr_014() -> None:
    import yaml

    m = yaml.safe_load((Path(__file__).resolve().parents[2] / "class_maps" / "ahmed2025.yaml").read_text(encoding="utf-8"))
    assert m["yolo_names"] == ["D", "d"]
    assert m["status"] == "decided"
    assert "014" in m["decision_ref"]
    assert m["map"] == {"D": "caries_permanent", "d": "caries_primary", "M": "__drop__", "m": "__drop__"}
