"""Patient-level (grouped) train/val/test split for canonical JSONL annotations.

Why: images of the same child (several views, retakes, near-duplicates) must never be spread
across train and test, otherwise evaluation is optimistic (PRD A1: split per child).

Grouping modes (``--group-mode``), strongest first:
- ``patient_field``  -- use ``patient_id`` already present in canonical records.
- ``metadata_csv``   -- ``--metadata-csv`` with columns ``image`` and ``--id-column``.
- ``regex``          -- ``--patient-regex`` with a named group ``(?P<patient>...)`` applied to the
                         image path (only valid after inspecting real filenames).
- ``exif_session``   -- HEURISTIC: images whose EXIF capture times are within ``--session-gap-s``
                         seconds of each other form one session ~ one patient. Default timeline is
                         global (``--session-folder-level 0``): Ahmed 2025 stores the 5 views of one
                         child in DIFFERENT view folders and used a single device.
- ``image``          -- every image is its own group (NO patient protection; recorded limitation).

``--merge-near-duplicates`` additionally unions groups that contain near-identical images
(64-bit dHash, Hamming distance <= ``--dhash-max-distance``). This catches duplicates/retakes
but NOT different views of the same mouth, so it never upgrades ``group_quality`` to "patient".

Outputs (``--out-dir``):
- ``splits.json``          -- deterministic assignment (no timestamps): {images: {image: split}, groups: ...}
- ``split_manifest.json``  -- sha256 of splits.json, seed, ratios, counts, group quality, limitations.

Ordering uses sha256(seed:group_id), so results are identical across machines/numpy versions.
Stdlib only, plus Pillow (EXIF, dHash) and numpy (dHash distance) when those modes are used.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

SPLIT_NAMES: tuple[str, ...] = ("train", "val", "test")
TOOL_VERSION = "1.0.0"

GROUP_QUALITY = {
    "patient_field": "patient",
    "metadata_csv": "patient",
    "regex": "patient_derived",
    "exif_session": "heuristic_session",
    "image": "none",
}

LIMITATIONS = {
    "patient": [],
    "patient_derived": ["Patient id derived from filenames by regex; correctness depends on the naming scheme."],
    "heuristic_session": [
        "Patient groups are HEURISTIC (EXIF capture-time sessions). Two children photographed within the "
        "session gap merge (harmless); one child photographed across a gap splits (leak risk).",
    ],
    "none": [
        "NO patient grouping: images of the same child may appear in different splits. Metrics are "
        "optimistic and CIs are not patient-level. Do not use for claims.",
    ],
}


class SplitError(ValueError):
    pass


# --------------------------------------------------------------------------------- loading


def load_canonical(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                records.append(json.loads(line))
    if not records:
        raise SplitError(f"{path}: no records")
    images = [r["image"] for r in records]
    if len(set(images)) != len(images):
        raise SplitError("duplicate image paths in canonical file")
    return records


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# -------------------------------------------------------------------------------- grouping


def group_by_field(records: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    missing = [r["image"] for r in records if not r.get("patient_id")]
    if missing:
        raise SplitError(f"{len(missing)} records have no patient_id (e.g. {missing[:3]})")
    for r in records:
        out[r["image"]] = f"pid:{r['patient_id']}"
    return out


def group_by_regex(images: Sequence[str], pattern: str) -> dict[str, str]:
    rx = re.compile(pattern)
    if "patient" not in rx.groupindex:
        raise SplitError("regex must contain a named group (?P<patient>...)")
    out: dict[str, str] = {}
    unmatched: list[str] = []
    for img in images:
        m = rx.search(img)
        if m is None:
            unmatched.append(img)
        else:
            out[img] = f"re:{m.group('patient')}"
    if unmatched:
        raise SplitError(f"{len(unmatched)} images do not match the regex (e.g. {unmatched[:3]})")
    return out


def group_by_metadata(images: Sequence[str], csv_path: Path, id_column: str) -> dict[str, str]:
    with csv_path.open(encoding="utf-8", newline="") as fh:
        rows = {row["image"]: row.get(id_column, "") for row in csv.DictReader(fh)}
    out: dict[str, str] = {}
    for img in images:
        pid = rows.get(img)
        if not pid:
            raise SplitError(f"metadata CSV has no {id_column!r} for {img}")
        out[img] = f"meta:{pid}"
    return out


def group_by_exif_session(
    timestamps: Mapping[str, datetime | None], gap_seconds: float, *, folder_level: int = 0
) -> dict[str, str]:
    """Sessions per timeline (global, or per ancestor folder ``folder_level`` > 0): sort by capture time; a gap > ``gap_seconds`` starts a new
    session. Images without a timestamp become singleton groups."""
    out: dict[str, str] = {}
    by_folder: dict[str, list[tuple[datetime, str]]] = {}
    for img, ts in timestamps.items():
        folder = str(PurePosixPath(img).parents[folder_level - 1]) if folder_level > 0 else ""
        if ts is None:
            out[img] = f"noexif:{img}"
        else:
            by_folder.setdefault(folder, []).append((ts, img))
    for folder, items in sorted(by_folder.items()):
        items.sort()
        session = 0
        prev: datetime | None = None
        for ts, img in items:
            if prev is not None and (ts - prev).total_seconds() > gap_seconds:
                session += 1
            out[img] = f"sess:{folder}:{session}"
            prev = ts
    return out


def read_exif_datetime(path: Path) -> datetime | None:  # pragma: no cover - thin Pillow wrapper
    from PIL import Image

    with Image.open(path) as im:
        exif = im.getexif()
        raw = exif.get_ifd(0x8769).get(36867) or exif.get(306)  # DateTimeOriginal, DateTime
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def dhash_bits(gray: Any, size: int = 8) -> Any:
    """Difference hash of a 2-D grayscale array already resized to (size, size+1) -> bool[size*size]."""
    import numpy as np

    arr = np.asarray(gray, dtype=np.int32)
    if arr.shape != (size, size + 1):
        raise SplitError(f"dhash expects shape {(size, size + 1)}, got {arr.shape}")
    return (arr[:, 1:] > arr[:, :-1]).reshape(-1)


def image_dhash(path: Path) -> Any:  # pragma: no cover - thin Pillow wrapper
    import numpy as np
    from PIL import Image

    with Image.open(path) as im:
        small = im.convert("L").resize((9, 8), Image.Resampling.BOX)
    return dhash_bits(np.asarray(small))


def near_duplicate_pairs(hashes: Mapping[str, Any], max_distance: int) -> list[tuple[str, str]]:
    import numpy as np

    keys = sorted(hashes)
    if not keys:
        return []
    bits = np.stack([np.asarray(hashes[k], dtype=bool) for k in keys])
    pairs: list[tuple[str, str]] = []
    for i in range(len(keys) - 1):
        dist = (bits[i + 1 :] != bits[i]).sum(axis=1)
        for j in np.nonzero(dist <= max_distance)[0]:
            pairs.append((keys[i], keys[i + 1 + int(j)]))
    return pairs


class UnionFind:
    def __init__(self, items: Sequence[str]) -> None:
        self.parent = {i: i for i in items}

    def find(self, x: str) -> str:
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # deterministic: lexicographically smaller root wins
            lo, hi = sorted((ra, rb))
            self.parent[hi] = lo


def merge_groups(image_to_group: Mapping[str, str], links: Sequence[tuple[str, str]]) -> dict[str, str]:
    """Union base groups that are connected by image-level links (e.g. near-duplicates)."""
    groups = sorted(set(image_to_group.values()))
    uf = UnionFind(groups)
    for a, b in links:
        uf.union(image_to_group[a], image_to_group[b])
    return {img: uf.find(g) for img, g in image_to_group.items()}


# ------------------------------------------------------------------------------- assignment


def _order_key(seed: int, gid: str) -> str:
    return hashlib.sha256(f"{seed}:{gid}".encode()).hexdigest()


def parse_ratios(text: str) -> tuple[float, float, float]:
    parts = [float(x) for x in text.split(",")]
    if len(parts) != 3 or any(p < 0 for p in parts) or abs(sum(parts) - 1.0) > 1e-6:
        raise SplitError("ratios must be three non-negative numbers summing to 1, e.g. 0.7,0.15,0.15")
    return parts[0], parts[1], parts[2]


def assign_splits(
    group_sizes: Mapping[str, int],
    ratios: Sequence[float],
    seed: int,
    *,
    strata: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Greedy deficit assignment of whole groups, per stratum, in sha256(seed:gid) order."""
    strata = strata or {}
    by_stratum: dict[str, list[str]] = {}
    for gid in group_sizes:
        by_stratum.setdefault(strata.get(gid, "_all"), []).append(gid)
    out: dict[str, str] = {}
    for stratum in sorted(by_stratum):
        gids = sorted(by_stratum[stratum], key=lambda g: _order_key(seed, g))
        total = sum(group_sizes[g] for g in gids)
        targets = [r * total for r in ratios]
        counts = [0.0] * len(ratios)
        for gid in gids:
            deficits = [targets[i] - counts[i] for i in range(len(ratios))]
            best = max(range(len(ratios)), key=lambda i: (deficits[i], -i))
            out[gid] = SPLIT_NAMES[best]
            counts[best] += group_sizes[gid]
    return out


def build_splits(
    image_to_group: Mapping[str, str],
    positives: Mapping[str, bool],
    ratios: Sequence[float],
    seed: int,
    *,
    stratify: bool = True,
) -> dict[str, Any]:
    """Return the deterministic ``splits.json`` payload."""
    sizes: dict[str, int] = {}
    group_pos: dict[str, bool] = {}
    for img, gid in image_to_group.items():
        sizes[gid] = sizes.get(gid, 0) + 1
        group_pos[gid] = group_pos.get(gid, False) or bool(positives.get(img, False))
    strata = {g: ("pos" if p else "neg") for g, p in group_pos.items()} if stratify else None
    group_split = assign_splits(sizes, ratios, seed, strata=strata)
    images = {img: group_split[gid] for img, gid in sorted(image_to_group.items())}
    return {
        "schema_version": 1,
        "seed": seed,
        "ratios": list(ratios),
        "stratified_by": "has_any_box" if stratify else None,
        "images": images,
        "groups": {gid: group_split[gid] for gid in sorted(group_split)},
        "image_groups": dict(sorted(image_to_group.items())),
    }


def check_no_leak(splits: Mapping[str, Any]) -> None:
    seen: dict[str, str] = {}
    for img, gid in splits["image_groups"].items():
        split = splits["images"][img]
        if seen.setdefault(gid, split) != split:
            raise SplitError(f"group {gid} appears in {seen[gid]} and {split}")


def serialize(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, sort_keys=True, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


def write_outputs(
    splits: Mapping[str, Any],
    out_dir: Path,
    *,
    group_mode: str,
    canonical_sha256: str | None,
    extra_limitations: Sequence[str] = (),
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    check_no_leak(splits)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = serialize(splits)
    (out_dir / "splits.json").write_bytes(data)
    quality = GROUP_QUALITY[group_mode]
    counts = {s: sum(1 for v in splits["images"].values() if v == s) for s in SPLIT_NAMES}
    gcounts = {s: sum(1 for v in splits["groups"].values() if v == s) for s in SPLIT_NAMES}
    manifest = {
        "tool": "ml/data/scripts/make_splits.py",
        "tool_version": TOOL_VERSION,
        "created_at": now().isoformat(),
        "splits_file": "splits.json",
        "splits_sha256": hashlib.sha256(data).hexdigest(),
        "canonical_sha256": canonical_sha256,
        "seed": splits["seed"],
        "ratios": splits["ratios"],
        "group_mode": group_mode,
        "group_quality": quality,
        "image_counts": counts,
        "group_counts": gcounts,
        "limitations": [*LIMITATIONS[quality], *extra_limitations],
    }
    (out_dir / "split_manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return manifest


# -------------------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--canonical", type=Path, required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--group-mode", choices=sorted(GROUP_QUALITY), required=True)
    p.add_argument("--patient-regex")
    p.add_argument("--metadata-csv", type=Path)
    p.add_argument("--id-column", default="patient_id")
    p.add_argument("--images-root", type=Path, help="needed for exif_session and --merge-near-duplicates")
    p.add_argument("--session-gap-s", type=float, default=120.0)
    p.add_argument("--session-folder-level", type=int, default=0, help="0 = one global timeline (single device)")
    p.add_argument("--merge-near-duplicates", action="store_true")
    p.add_argument("--dhash-max-distance", type=int, default=4)
    p.add_argument("--ratios", default="0.7,0.15,0.15")
    p.add_argument("--seed", type=int, default=20261008)
    p.add_argument("--no-stratify", action="store_true")
    args = p.parse_args(argv)

    try:
        records = load_canonical(args.canonical)
        images = [r["image"] for r in records]
        mode = args.group_mode
        if mode == "patient_field":
            groups = group_by_field(records)
        elif mode == "metadata_csv":
            if args.metadata_csv is None:
                raise SplitError("--metadata-csv required")
            groups = group_by_metadata(images, args.metadata_csv, args.id_column)
        elif mode == "regex":
            if not args.patient_regex:
                raise SplitError("--patient-regex required")
            groups = group_by_regex(images, args.patient_regex)
        elif mode == "exif_session":
            if args.images_root is None:
                raise SplitError("--images-root required")
            stamps = {img: read_exif_datetime(args.images_root / img) for img in images}
            groups = group_by_exif_session(stamps, args.session_gap_s, folder_level=args.session_folder_level)
        else:
            groups = {img: f"img:{img}" for img in images}
        extra: list[str] = []
        if args.merge_near_duplicates:
            if args.images_root is None:
                raise SplitError("--images-root required")
            hashes = {img: image_dhash(args.images_root / img) for img in images}
            pairs = near_duplicate_pairs(hashes, args.dhash_max_distance)
            groups = merge_groups(groups, pairs)
            extra.append(f"Near-duplicate merge applied (dHash <= {args.dhash_max_distance}); {len(pairs)} links.")
        positives = {r["image"]: bool(r.get("boxes")) for r in records}
        splits = build_splits(groups, positives, parse_ratios(args.ratios), args.seed, stratify=not args.no_stratify)
        manifest = write_outputs(splits, args.out_dir, group_mode=mode,
                                 canonical_sha256=sha256_file(args.canonical), extra_limitations=extra)
    except SplitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({k: manifest[k] for k in ("splits_sha256", "group_quality", "image_counts", "group_counts")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
