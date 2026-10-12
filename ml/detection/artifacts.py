"""Package what leaves (or enters) a hosted notebook -- and refuse anything that must not.

    python artifacts.py collect --out-dir /kaggle/working/download --run-dir ml/runs/<id> \
        --bundle ml/exports/0.1.0-dev.1 --file out/report_test.json --file out/parity.json ...
    python artifacts.py pack-code --out dentisai-ml-code.zip [--with-processed]

``collect`` copies the run record, eval/parity/threshold reports and the contract bundle into
``--out-dir``, writes ``RUN_SUMMARY.json`` (sha256 + size of every file) and a single zip. It
REFUSES photos (by extension AND magic bytes), ``.npy`` arrays (calibration tensors are derived
from photos), checkpoints/archives and anything above ``--max-file-mb``: hosted-notebook outputs
must never carry images (backend/CLAUDE.md §3.5).

``pack-code`` zips the pipeline code needed on Kaggle/Colab (ml/detection without .venv/caches
+ ml/data/scripts, class_maps, the committed splits/, license_gate.yaml, LICENSES.md) for upload as a PRIVATE Kaggle
dataset. ``--with-processed`` adds ``ml/data/processed/ahmed2025`` annotation files
(canonical.jsonl, splits/*.json, verify_report.json) -- text only; images are never packed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ML = HERE.parent
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tif", ".tiff", ".heic", ".heif", ".dng"}
FORBIDDEN_EXT = IMAGE_EXT | {".npy", ".npz", ".pth", ".pt", ".ckpt", ".h5", ".zip", ".tar", ".gz", ".tgz", ".7z", ".mp4", ".mov"}
MAGIC = (b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"BM", b"II*\x00", b"MM\x00*", b"\x93NUMPY", b"PK\x03\x04")
SKIP_DIRS = {".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "out", "dist", ".ipynb_checkpoints"}
PROCESSED_FILES = ("canonical.jsonl", "verify_report.json", "splits/splits.json", "splits/split_manifest.json")


class ArtifactError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def forbidden_reason(path: Path) -> str | None:
    """Why ``path`` must not be exported, or None. ``model.tflite`` is allowed (TFL3 flatbuffer)."""
    ext = path.suffix.lower()
    if ext in FORBIDDEN_EXT:
        return f"forbidden file type {ext}"
    head = path.read_bytes()[:12]
    if any(head.startswith(m) for m in MAGIC) or (head[:4] == b"RIFF" and head[8:12] == b"WEBP") or head[4:8] == b"ftyp":
        return "content looks like an image/array/archive (magic bytes)"
    return None


def _walk(paths: Iterable[Path]) -> list[tuple[Path, Path]]:
    """(source file, relative destination) for files and directories (recursively)."""
    out: list[tuple[Path, Path]] = []
    for p in paths:
        if p.is_dir():
            out += [(f, Path(p.name) / f.relative_to(p)) for f in sorted(p.rglob("*")) if f.is_file()]
        elif p.is_file():
            out.append((p, Path(p.name)))
        else:
            raise ArtifactError(f"not found: {p}")
    return out


def collect(out_dir: Path, inputs: Sequence[Path], *, max_file_mb: float = 25.0, now: datetime | None = None,
            extra: dict[str, Any] | None = None) -> dict[str, Any]:
    pairs = _walk(inputs)
    problems = [f"{src}: {r}" for src, _ in pairs if (r := forbidden_reason(src))]
    problems += [f"{src}: {src.stat().st_size / 2**20:.1f} MiB > {max_file_mb} MiB" for src, _ in pairs
                 if src.stat().st_size > max_file_mb * 2**20]
    dests = [d.as_posix() for _, d in pairs]
    problems += [f"duplicate destination {d}" for d in sorted({d for d in dests if dests.count(d) > 1})]
    if problems:
        raise ArtifactError("refusing to export: " + "; ".join(problems))
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    files = []
    for src, rel in pairs:
        dst = out_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        files.append({"path": rel.as_posix(), "bytes": dst.stat().st_size, "sha256": sha256_file(dst)})
    summary = {
        "created_at": (now or datetime.now(UTC)).isoformat(),
        "files": files,
        "contains_images": False,
        "claims": "none -- technical bake-off artifacts, NOT VALIDATED, not a clinical accuracy claim",
        **(extra or {}),
    }
    (out_dir / "RUN_SUMMARY.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    archive = out_dir.with_suffix(".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(out_dir.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(out_dir.parent).as_posix())
    summary["archive"] = str(archive)
    return summary


def code_files(ml_root: Path = ML, *, with_processed: bool = False) -> list[tuple[Path, str]]:
    det = ml_root / "detection"
    out: list[tuple[Path, str]] = []
    for f in sorted(det.rglob("*")):
        rel = f.relative_to(ml_root)
        if f.is_file() and not (set(rel.parts) & SKIP_DIRS) and f.suffix not in {".pyc", ".tflite", ".onnx", ".npy"}:
            out.append((f, rel.as_posix()))
    data = ml_root / "data"
    for name in ("license_gate.yaml", "LICENSES.md", "README.md"):
        if (data / name).is_file():
            out.append((data / name, f"data/{name}"))
    for sub in ("scripts", "class_maps"):
        for f in sorted((data / sub).glob("*")):
            if f.is_file() and f.suffix in {".py", ".md", ".yaml", ".yml", ".csv"}:
                out.append((f, f"data/{sub}/{f.name}"))
    for f in sorted((data / "splits").rglob("*")):  # committed, sha256-pinned patient-level splits (text only)
        if f.is_file() and f.suffix in {".json", ".csv"}:
            out.append((f, f"data/{f.relative_to(data).as_posix()}"))
    if with_processed:
        proc = data / "processed" / "ahmed2025"
        for name in PROCESSED_FILES:
            if (proc / name).is_file():
                out.append((proc / name, f"data/processed/ahmed2025/{name}"))
    for src, name in out:
        if (r := forbidden_reason(src)) is not None:
            raise ArtifactError(f"refusing to pack {name}: {r}")
    return out


def pack_code(archive: Path, *, ml_root: Path = ML, with_processed: bool = False) -> dict[str, Any]:
    files = code_files(ml_root, with_processed=with_processed)
    archive.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for src, rel in files:
            data = src.read_bytes()
            h.update(rel.encode() + b"\0" + hashlib.sha256(data).digest())
            info = zipfile.ZipInfo(f"ml/{rel}", date_time=(2026, 1, 1, 0, 0, 0))  # deterministic archive
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    return {"archive": str(archive), "files": len(files), "code_sha256": h.hexdigest(), "with_processed": with_processed}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--out-dir", type=Path, required=True)
    c.add_argument("--run-dir", type=Path)
    c.add_argument("--bundle", type=Path)
    c.add_argument("--file", type=Path, action="append", default=[])
    c.add_argument("--max-file-mb", type=float, default=25.0)
    k = sub.add_parser("pack-code")
    k.add_argument("--out", type=Path, required=True)
    k.add_argument("--with-processed", action="store_true")
    args = p.parse_args(argv)
    try:
        if args.cmd == "collect":
            inputs = [x for x in (args.run_dir, args.bundle) if x is not None] + list(args.file)
            res = collect(args.out_dir, inputs, max_file_mb=args.max_file_mb)
        else:
            res = pack_code(args.out, with_processed=args.with_processed)
    except ArtifactError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({k: v for k, v in res.items() if k != "files"} | {"n_files": len(res["files"]) if isinstance(res["files"], list) else res["files"]},
                     indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
