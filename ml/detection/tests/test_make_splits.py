"""make_splits.py on SYNTHETIC records: no patient leaks, determinism, manifest hash."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import make_splits as ms


def synthetic_records(n_patients: int = 60, views: int = 4, seed: int = 1) -> list[dict[str, Any]]:
    rng = np.random.default_rng(seed)
    recs = []
    for p in range(n_patients):
        positive = bool(rng.random() < 0.4)
        for v in range(int(rng.integers(1, views + 1))):
            recs.append({"image": f"Images/view{v}/P{p:03d}_v{v}.jpg", "width": 10, "height": 10, "patient_id": f"P{p:03d}",
                         "boxes": [{"cls": "caries_cavitated", "x1": 1, "y1": 1, "x2": 3, "y2": 3}] if positive and v == 0 else []})
    return recs


def write_canonical(path: Path, recs: list[dict[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
    return path


def patients_per_split(splits: dict[str, Any], recs: list[dict[str, Any]]) -> dict[str, set[str]]:
    pid = {r["image"]: r["patient_id"] for r in recs}
    out: dict[str, set[str]] = {s: set() for s in ms.SPLIT_NAMES}
    for img, s in splits["images"].items():
        out[s].add(pid[img])
    return out


@pytest.mark.parametrize("seed", [0, 1, 20261008])
def test_no_patient_leak_and_ratios(seed: int) -> None:
    recs = synthetic_records()
    groups = ms.group_by_field(recs)
    pos = {r["image"]: bool(r["boxes"]) for r in recs}
    splits = ms.build_splits(groups, pos, (0.7, 0.15, 0.15), seed)
    ms.check_no_leak(splits)
    per = patients_per_split(splits, recs)
    assert not (per["train"] & per["val"]) and not (per["train"] & per["test"]) and not (per["val"] & per["test"])
    n = len(recs)
    counts = {s: sum(1 for v in splits["images"].values() if v == s) for s in ms.SPLIT_NAMES}
    assert sum(counts.values()) == n
    assert abs(counts["train"] / n - 0.7) < 0.1
    assert counts["val"] > 0 and counts["test"] > 0
    # stratification: every split gets positive patients
    pos_patients = {r["patient_id"] for r in recs if r["boxes"]}
    assert all(per[s] & pos_patients for s in ms.SPLIT_NAMES)


def test_deterministic_and_seed_sensitive(tmp_path: Path) -> None:
    recs = synthetic_records()
    canon = write_canonical(tmp_path / "c.jsonl", recs)
    a = tmp_path / "a"
    b = tmp_path / "b"
    c = tmp_path / "c"
    assert ms.main(["--canonical", str(canon), "--out-dir", str(a), "--group-mode", "patient_field"]) == 0
    assert ms.main(["--canonical", str(canon), "--out-dir", str(b), "--group-mode", "patient_field"]) == 0
    assert ms.main(["--canonical", str(canon), "--out-dir", str(c), "--group-mode", "patient_field", "--seed", "7"]) == 0
    assert (a / "splits.json").read_bytes() == (b / "splits.json").read_bytes()
    assert (a / "splits.json").read_bytes() != (c / "splits.json").read_bytes()
    man = json.loads((a / "split_manifest.json").read_text(encoding="utf-8"))
    assert man["splits_sha256"] == hashlib.sha256((a / "splits.json").read_bytes()).hexdigest()
    assert man["canonical_sha256"] == hashlib.sha256(canon.read_bytes()).hexdigest()
    assert man["group_quality"] == "patient" and man["limitations"] == []
    # record order in the input must not matter
    canon2 = write_canonical(tmp_path / "c2.jsonl", list(reversed(recs)))
    assert ms.main(["--canonical", str(canon2), "--out-dir", str(tmp_path / "d"), "--group-mode", "patient_field"]) == 0
    assert (tmp_path / "d" / "splits.json").read_bytes() == (a / "splits.json").read_bytes()


def test_regex_and_metadata_modes(tmp_path: Path) -> None:
    recs = synthetic_records(n_patients=20)
    for r in recs:
        r.pop("patient_id")
    canon = write_canonical(tmp_path / "c.jsonl", recs)
    assert ms.main(["--canonical", str(canon), "--out-dir", str(tmp_path / "r"), "--group-mode", "regex",
                    "--patient-regex", r"(?P<patient>P\d+)_v"]) == 0
    s = json.loads((tmp_path / "r" / "splits.json").read_text(encoding="utf-8"))
    ms.check_no_leak(s)
    assert all(g.startswith("re:P") for g in s["groups"])
    man = json.loads((tmp_path / "r" / "split_manifest.json").read_text(encoding="utf-8"))
    assert man["group_quality"] == "patient_derived" and man["limitations"]
    meta = tmp_path / "meta.csv"
    with meta.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["image", "patient_id"])
        for r in recs:
            w.writerow([r["image"], r["image"].split("/")[-1][:4]])
    assert ms.main(["--canonical", str(canon), "--out-dir", str(tmp_path / "m"), "--group-mode", "metadata_csv",
                    "--metadata-csv", str(meta)]) == 0
    s2 = json.loads((tmp_path / "m" / "splits.json").read_text(encoding="utf-8"))
    # same patient key (P000..) from the CSV as from the regex -> identical group partition
    assert sorted(g[5:] for g in s2["groups"]) == sorted(g[3:] for g in s["groups"])
    ms.check_no_leak(s2)


@pytest.mark.parametrize(
    ("args", "msg"),
    [
        (["--group-mode", "patient_field"], "no patient_id"),
        (["--group-mode", "regex"], "--patient-regex required"),
        (["--group-mode", "regex", "--patient-regex", "(P)"], "named group"),
        (["--group-mode", "regex", "--patient-regex", "(?P<patient>ZZZ)"], "do not match"),
        (["--group-mode", "metadata_csv"], "--metadata-csv required"),
        (["--group-mode", "exif_session"], "--images-root required"),
        (["--group-mode", "image", "--merge-near-duplicates"], "--images-root required"),
        (["--group-mode", "image", "--ratios", "0.5,0.5"], "ratios"),
    ],
)
def test_cli_errors(tmp_path: Path, args: list[str], msg: str, capsys: pytest.CaptureFixture[str]) -> None:
    canon = write_canonical(tmp_path / "c.jsonl", [{"image": "a.jpg", "boxes": []}, {"image": "b.jpg", "boxes": []}])
    assert ms.main(["--canonical", str(canon), "--out-dir", str(tmp_path / "o"), *args]) == 2
    assert msg in capsys.readouterr().err


def test_metadata_missing_id(tmp_path: Path) -> None:
    meta = tmp_path / "m.csv"
    meta.write_text("image,patient_id\na.jpg,\n", encoding="utf-8")
    with pytest.raises(ms.SplitError, match="no 'patient_id'"):
        ms.group_by_metadata(["a.jpg"], meta, "patient_id")


def test_image_mode_records_limitation(tmp_path: Path) -> None:
    canon = write_canonical(tmp_path / "c.jsonl", synthetic_records(10))
    assert ms.main(["--canonical", str(canon), "--out-dir", str(tmp_path / "o"), "--group-mode", "image", "--no-stratify"]) == 0
    man = json.loads((tmp_path / "o" / "split_manifest.json").read_text(encoding="utf-8"))
    assert man["group_quality"] == "none"
    assert "NO patient grouping" in man["limitations"][0]


def test_load_canonical_errors(tmp_path: Path) -> None:
    empty = tmp_path / "e.jsonl"
    empty.write_text("\n", encoding="utf-8")
    with pytest.raises(ms.SplitError, match="no records"):
        ms.load_canonical(empty)
    dup = write_canonical(tmp_path / "d.jsonl", [{"image": "a"}, {"image": "a"}])
    with pytest.raises(ms.SplitError, match="duplicate"):
        ms.load_canonical(dup)


def test_exif_sessions() -> None:
    t0 = datetime(2025, 1, 1, 9, 0, 0)
    stamps: dict[str, datetime | None] = {
        "f/a.jpg": t0, "f/b.jpg": t0 + timedelta(seconds=30), "f/c.jpg": t0 + timedelta(seconds=400),
        "g/d.jpg": t0 + timedelta(seconds=10),  # other folder -> other session
        "f/e.jpg": None,
    }
    g = ms.group_by_exif_session(stamps, gap_seconds=120, folder_level=1)
    assert g["f/a.jpg"] == g["f/b.jpg"] != g["f/c.jpg"]
    assert g["g/d.jpg"] not in (g["f/a.jpg"], g["f/c.jpg"])
    assert g["f/e.jpg"] == "noexif:f/e.jpg"
    glob = ms.group_by_exif_session(stamps, gap_seconds=120)  # global timeline (default)
    assert glob["f/a.jpg"] == glob["g/d.jpg"] == glob["f/b.jpg"] == "sess::0" and glob["f/c.jpg"] == "sess::1"


def test_dhash_and_near_duplicate_merge() -> None:
    base = np.arange(72).reshape(8, 9) % 7
    h1 = ms.dhash_bits(base)
    h2 = ms.dhash_bits(base)
    h3 = ms.dhash_bits(base[:, ::-1])
    assert h1.shape == (64,)
    with pytest.raises(ms.SplitError):
        ms.dhash_bits(np.zeros((8, 8)))
    pairs = ms.near_duplicate_pairs({"a": h1, "b": h2, "c": h3}, max_distance=0)
    assert pairs == [("a", "b")]
    assert ms.near_duplicate_pairs({}, 4) == []
    merged = ms.merge_groups({"a": "g1", "b": "g2", "c": "g3"}, pairs)
    assert merged["a"] == merged["b"] == "g1" and merged["c"] == "g3"


def test_union_find_path_compression() -> None:
    uf = ms.UnionFind(["a", "b", "c", "d"])
    uf.union("c", "d")
    uf.union("b", "c")
    uf.union("a", "b")
    uf.union("a", "d")  # already joined
    assert {uf.find(x) for x in "abcd"} == {"a"}


def test_leak_detection() -> None:
    with pytest.raises(ms.SplitError, match="appears in"):
        ms.check_no_leak({"image_groups": {"a": "g", "b": "g"}, "images": {"a": "train", "b": "test"}})


def test_write_outputs_extra_limitations(tmp_path: Path) -> None:
    splits = ms.build_splits({"a": "g1", "b": "g2"}, {}, (1.0, 0.0, 0.0), 0)
    man = ms.write_outputs(splits, tmp_path, group_mode="exif_session", canonical_sha256=None, extra_limitations=["x"],
                           now=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    assert man["limitations"][-1] == "x" and man["created_at"].startswith("2026-01-01")
    assert man["image_counts"] == {"train": 2, "val": 0, "test": 0}


def test_sha256_file(tmp_path: Path) -> None:
    p = tmp_path / "x"
    p.write_bytes(b"abc")
    assert ms.sha256_file(p) == hashlib.sha256(b"abc").hexdigest()
