"""Small helpers shared by the Kaggle/Colab notebooks (``*_kaggle.py``). Stdlib + PyYAML only.

Everything that decides something (licence, metrics, export) lives in the pipeline modules;
this file only finds paths, fetches the PUBLIC Ahmed 2025 data, runs commands, builds venvs from
pinned lock files, writes run configs and environment records. Network/subprocess calls are
injectable so the logic is unit-tested without either (``tests/test_kaggle_tools.py``,
``tests/test_nbkit_data.py``); ``kaggle/local_simulate.py`` runs the notebook cells end to end.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import importlib.util
import json
import os
import shlex
import shutil
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
import zlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from bootstrap import compute_env, default_dirs, free_gb

__all__ = ["compute_env", "default_dirs", "free_gb"]  # re-exported (bootstrap is inlined into notebook cell 1)

#: Bumped when notebooks need new nbkit functions (bootstrap.REQUIRED_NBKIT_API checks it).
NBKIT_API = 2
HERE = Path(__file__).resolve().parent
LOCKS = HERE / "requirements"

#: uv for fast, reproducible venvs (exists on PyPI, checked 2026-10-11).
UV_VERSION = "0.12.7"
#: Conversion / evaluation env (Python 3.12: onnx2tf 2.6.9 requires >= 3.12 and pins its deps exactly).
EXPORT_PY = "3.12"
EXPORT_LOCK = LOCKS / "export_py312.txt"            # onnx2tf 2.6.9 + LiteRT + pipeline deps (no TensorFlow)
EXPORT_TF_LOCK = LOCKS / "export_tf_py312.txt"      # same + tensorflow 2.21.0 for onnx2tf -tb tf_converter (YOLOX)
#: Model Maker env (Python 3.10; mediapipe-model-maker 0.2.1.4 needs tensorflow<2.16 -> TF 2.15.1 + CUDA 12.2 wheels).
MM_PY = "3.10"
MM_LOCK = LOCKS / "mm_py310.txt"
#: Kept for reference / old notebooks: the top-level pins the locks were compiled from.
EXPORT_PKGS = ("onnx2tf==2.6.9", "onnx==1.20.1", "onnxruntime==1.26.0", "onnxsim==0.6.5", "ai-edge-litert==2.1.2", "numpy==2.2.6",
               "pillow>=10", "pyyaml>=6", "jsonschema>=4.18", "defusedxml>=0.7.1")
EXPORT_TF_PKGS = ("tensorflow==2.21.0", "tf-keras==2.21.0", "keras==3.15.0")
MM_PKGS = ("mediapipe-model-maker==0.2.1.4", "tensorflow[and-cuda]==2.15.1", "mediapipe==0.10.14")
#: import name -> pip spec, installed into the KERNEL only when missing (never touches the kernel's torch).
KERNEL_MODULES = {"yaml": "pyyaml>=6", "jsonschema": "jsonschema>=4.18", "defusedxml": "defusedxml>=0.7.1", "PIL": "pillow>=10"}
YOLOX_KERNEL_MODULES = {"loguru": "loguru==0.7.3", "tabulate": "tabulate==0.9.0", "pycocotools": "pycocotools==2.0.10",
                        "thop": "thop==0.1.1.post2209072238", "ninja": "ninja==1.13.0", "psutil": "psutil",
                        "tensorboard": "tensorboard", "cv2": "opencv-python-headless==4.11.0.86", "onnx": "onnx==1.20.1",
                        "tqdm": "tqdm"}  # = YOLOX requirements.txt minus torch/torchvision (never reinstalled: Kaggle ships them)

# --------------------------------------------------------------------- Ahmed 2025 (public, CC BY 4.0)
AHMED_RECORD = "14827784"
AHMED_ZIP = "Dataset.zip"
AHMED_SIZE = 1_576_115_314
AHMED_MD5 = "89307871ac2f08f4a8f3a7da1f18db31"
AHMED_URLS = (f"https://zenodo.org/api/records/{AHMED_RECORD}/files/{AHMED_ZIP}/content",
              f"https://zenodo.org/records/{AHMED_RECORD}/files/{AHMED_ZIP}?download=1")
AHMED_MARKER = "Dataset/Images"
AHMED_IMAGES = "Dataset/Images"
AHMED_LABELS = "Dataset/Annotations/Darknet_YOLO"
AHMED_ATTRIBUTION = ('Contains data from "Annotated intraoral image dataset for dental caries detection" by S. M. F. Ahmed, '
                     "H. Ghori, A. Khalid, A. Nooruddin, N. Adnan, A. Lal and F. Umer (Aga Khan University), Zenodo, "
                     "doi:10.5281/zenodo.14827784, licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/).")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
#: SMOKE_TEST subset size per split (positives preferred; same split assignment as the full run).
SMOKE_N = {"train": 16, "val": 8, "test": 8}
UA = "DentisAI-notebook/2 (+https://zenodo.org/records/14827784; research use)"


class DataError(RuntimeError):
    pass


def find_dir(roots: Sequence[Path], marker: str, *, max_depth: int = 4) -> Path | None:
    """First directory D under ``roots`` (breadth-first, sorted) such that ``D / marker`` exists."""
    for root in roots:
        frontier = [root] if root.is_dir() else []
        for _ in range(max_depth + 1):
            nxt: list[Path] = []
            for d in frontier:
                if (d / marker).exists():
                    return d
                try:
                    nxt += sorted(p for p in d.iterdir() if p.is_dir() and not p.name.startswith("."))
                except OSError:
                    continue
            frontier = nxt
    return None


def find_named(roots: Sequence[Path], name: str, *, want_dir: bool, max_depth: int = 6,
               exclude: Sequence[str] = ()) -> list[Path]:
    """All paths called ``name`` under ``roots`` (depth-limited), skipping paths containing any ``exclude`` part."""
    out: list[Path] = []
    for root in roots:
        frontier = [root] if root.is_dir() else []
        for _ in range(max_depth + 1):
            nxt: list[Path] = []
            for d in frontier:
                try:
                    kids = sorted(d.iterdir())
                except OSError:
                    continue
                for k in kids:
                    if any(x in k.as_posix() for x in exclude):
                        continue
                    if k.name == name and (k.is_dir() if want_dir else k.is_file()):
                        out.append(k)
                    elif k.is_dir() and not k.name.startswith("."):
                        nxt.append(k)
            frontier = nxt
    return out


def deep_merge(base: Mapping[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(dict(base))
    for k, v in over.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, Mapping) and isinstance(out.get(k), Mapping) else copy.deepcopy(v)
    return out


def write_run_config(base_cfg: Path, out: Path, overrides: Mapping[str, Any]) -> dict[str, Any]:
    """Copy a repo config with notebook-specific paths. Refuses to touch ``license_verified`` or ``data.datasets``:
    licence status is decided by the repo config + license gate, never by a notebook."""
    for forbidden in ("license_verified", "candidate"):
        if forbidden in overrides:
            raise ValueError(f"notebook must not override {forbidden!r}")
    if "datasets" in overrides.get("data", {}):
        raise ValueError("notebook must not override data.datasets")
    cfg = deep_merge(yaml.safe_load(base_cfg.read_text(encoding="utf-8")), overrides)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return cfg


def metadata_from_splits(splits: Path, out_csv: Path, *, source: str = "ahmed2025") -> int:
    """eval.py metadata CSV with ``patient_id`` = split group id (so bootstrap CIs resample groups, not images)."""
    data = json.loads(splits.read_text(encoding="utf-8"))
    groups: Mapping[str, str] = data.get("image_groups") or {}
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["image", "patient_id", "source"])
        for img in sorted(data["images"]):
            w.writerow([img, groups.get(img, ""), source])
    return len(data["images"])


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def md5_file(path: Path, progress: Callable[[int], None] | None = None) -> str:
    h = hashlib.md5()  # Zenodo publishes MD5; integrity check, not security
    done = 0
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done)
    return h.hexdigest()


def processed_paths(processed: Path) -> dict[str, Path]:
    return {"canonical": processed / "canonical.jsonl", "splits": processed / "splits" / "splits.json",
            "manifest": processed / "splits" / "split_manifest.json"}


def check_processed(processed: Path) -> dict[str, Any] | None:
    """None if the processed files are absent; otherwise verify splits.json against split_manifest.json."""
    p = processed_paths(processed)
    if not all(v.is_file() for v in p.values()):
        return None
    manifest = json.loads(p["manifest"].read_text(encoding="utf-8"))
    actual = sha256_file(p["splits"])
    if manifest.get("splits_sha256") != actual:
        raise ValueError(f"splits.json sha256 {actual} != split_manifest.json {manifest.get('splits_sha256')}")
    return {"canonical_sha256": sha256_file(p["canonical"]), "splits_sha256": actual,
            "group_quality": manifest.get("group_quality"), "image_counts": manifest.get("image_counts"),
            "smoke_subset_of": manifest.get("smoke_subset_of")}


def class_map_problems(path: Path) -> list[str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    probs = [f"source label {k!r} is null (undecided)" for k, v in (data.get("map") or {}).items() if v is None]
    if data.get("status") not in ("reviewed", "decided"):  # 'decided' = product-owner decision (ADR 014), no drg review
        probs.append(f"class map status is {data.get('status')!r} (not 'decided'/'reviewed'): runs are technical experiments only")
    return probs


def pick_onnx2tf_outputs(folder: Path) -> dict[str, Path | None]:
    """onnx2tf -oiqt writes <name>_float32.tflite, _integer_quant.tflite (float I/O) and
    _full_integer_quant.tflite (int8 I/O). The float-I/O INT8 file is preferred for the app: the raw YOLOX
    tensor mixes box offsets and probabilities, so an int8 OUTPUT would share one scale for both."""
    out: dict[str, Path | None] = {"float32": None, "int8_float_io": None, "int8_full": None}
    for p in sorted(folder.glob("*.tflite")):
        name = p.name
        if "_with_int16_act" in name:
            continue
        if name.endswith("_full_integer_quant.tflite"):
            out["int8_full"] = out["int8_full"] or p
        elif name.endswith("_integer_quant.tflite"):
            out["int8_float_io"] = out["int8_float_io"] or p
        elif name.endswith("_float32.tflite"):
            out["float32"] = out["float32"] or p
    return out


def next_dev_version(exports: Path, base: str = "0.1.0") -> str:
    """``0.1.0-dev.N`` with N one higher than any existing bundle dir (bundles are immutable)."""
    n = 0
    if exports.is_dir():
        for d in exports.iterdir():
            if d.name.startswith(f"{base}-dev."):
                tail = d.name.rsplit(".", 1)[-1]
                if tail.isdigit():
                    n = max(n, int(tail))
    return f"{base}-dev.{n + 1}"


# ------------------------------------------------------------------------------------------- HTTP

Opener = Callable[..., Any]


class RangeNotSupportedError(DataError):
    pass


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (408, 425, 429) or exc.code >= 500
    return isinstance(exc, urllib.error.URLError | OSError | TimeoutError | ConnectionError | EOFError)


def fetch_range(url: str, start: int, end: int, *, opener: Opener = urllib.request.urlopen, retries: int = 8,
                timeout: float = 120.0, sleep: Callable[[float], None] = time.sleep) -> bytes:
    """Bytes ``start..end`` (inclusive) with retries + exponential backoff; refuses servers that ignore Range."""
    want = end - start + 1
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": f"bytes={start}-{end}"})
            with opener(req, timeout=timeout) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                if status != 206:
                    raise RangeNotSupportedError(f"{url}: HTTP {status} for a Range request")
                data: bytes = resp.read()
            if len(data) != want:
                raise EOFError(f"short read {len(data)}/{want} bytes")
            return data
        except RangeNotSupportedError:
            raise
        except Exception as exc:
            if attempt >= retries or not _retryable(exc):
                raise DataError(f"download failed after {attempt + 1} attempts ({start}-{end}): {exc}") from exc
            sleep(min(60.0, 2.0 * 2**attempt))
    raise AssertionError("unreachable")  # pragma: no cover


def pick_url(urls: Sequence[str], size: int, *, opener: Opener = urllib.request.urlopen,
             sleep: Callable[[float], None] = time.sleep) -> str:
    """First URL that answers a 1-byte Range request at the expected end of the file."""
    errors = []
    for url in urls:
        try:
            fetch_range(url, size - 1, size - 1, opener=opener, retries=3, timeout=60, sleep=sleep)
            return url
        except DataError as exc:
            errors.append(f"{url}: {exc}")
    raise DataError("no download URL works: " + " | ".join(errors))


def download_parallel(urls: Sequence[str], dest: Path, *, size: int, md5: str, workers: int = 8, chunk: int = 16 << 20,
                      opener: Opener = urllib.request.urlopen, sleep: Callable[[float], None] = time.sleep,
                      log: Callable[[str], None] = print) -> Path:
    """Resumable parallel Range download into ``dest`` (``.part`` + ``.parts.json`` until the MD5 matches)."""
    ok_marker = dest.with_name(dest.name + ".md5ok")
    if dest.is_file() and dest.stat().st_size == size and ok_marker.is_file() and ok_marker.read_text().strip() == md5:
        log(f"{dest.name}: already downloaded and verified")
        return dest
    url = pick_url(urls, size, opener=opener, sleep=sleep)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part, state_path = dest.with_name(dest.name + ".part"), dest.with_name(dest.name + ".parts.json")
    n_chunks = (size + chunk - 1) // chunk
    done: set[int] = set()
    if part.is_file() and part.stat().st_size == size and state_path.is_file():
        st = json.loads(state_path.read_text(encoding="utf-8"))
        if st.get("size") == size and st.get("chunk") == chunk:
            done = {int(i) for i in st.get("done", [])}
    else:
        with part.open("wb") as fh:
            fh.truncate(size)
    lock = threading.Lock()
    t0, start_done = time.time(), len(done)
    log(f"download {url} -> {dest} ({size / 2**30:.2f} GiB, {n_chunks - len(done)}/{n_chunks} chunks to go, {workers} connections)")

    def one(i: int) -> None:
        a, b = i * chunk, min(size, (i + 1) * chunk) - 1
        data = fetch_range(url, a, b, opener=opener, sleep=sleep)
        with lock:
            with part.open("r+b") as fh:
                fh.seek(a)
                fh.write(data)
            done.add(i)
            state_path.write_text(json.dumps({"size": size, "chunk": chunk, "done": sorted(done)}), encoding="utf-8")
            k = len(done)
            if k == n_chunks or k % max(1, n_chunks // 20) == 0:
                rate = (k - start_done) * chunk / max(1e-6, time.time() - t0)
                eta = (n_chunks - k) * chunk / max(rate, 1.0)
                log(f"  {100 * k / n_chunks:5.1f}%  {rate / 2**20:6.2f} MiB/s  ETA {eta / 60:5.1f} min")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, [i for i in range(n_chunks) if i not in done]))
    log("verifying MD5 ...")
    got = md5_file(part)
    if got != md5:
        part.unlink()
        state_path.unlink(missing_ok=True)
        raise DataError(f"MD5 mismatch for {dest.name}: {got} != {md5} (partial file deleted; run the cell again)")
    part.replace(dest)
    state_path.unlink(missing_ok=True)
    ok_marker.write_text(md5 + "\n", encoding="utf-8")
    log(f"{dest.name}: MD5 OK ({md5})")
    return dest


def fetch_url(url: str, dest: Path, *, opener: Opener = urllib.request.urlopen, retries: int = 5,
              sleep: Callable[[float], None] = time.sleep) -> Path:
    """Small file download (e.g. pretrained weights) with retries; atomic rename. Returns ``dest``."""
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with opener(req, timeout=300) as resp, tmp.open("wb") as fh:
                shutil.copyfileobj(resp, fh, 1 << 20)
            tmp.replace(dest)
            return dest
        except Exception as exc:
            if attempt >= retries or not _retryable(exc):
                raise DataError(f"download of {url} failed: {exc}") from exc
            sleep(min(60.0, 2.0 * 2**attempt))
    raise AssertionError("unreachable")  # pragma: no cover


class RemoteFile:
    """Read-only, seekable view of a remote file through HTTP Range requests (for ``zipfile``)."""

    def __init__(self, url: str, size: int, *, opener: Opener = urllib.request.urlopen, block: int = 1 << 20,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.url, self.size, self.opener, self.block, self.sleep = url, size, opener, block, sleep
        self.pos, self._cache_start, self._cache = 0, 0, b""
        self.requests = 0

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = 0) -> int:
        base = {0: 0, 1: self.pos, 2: self.size}[whence]
        self.pos = max(0, base + offset)
        return self.pos

    def read(self, n: int | None = -1) -> bytes:
        if n is None or n < 0:
            n = self.size - self.pos
        n = min(n, self.size - self.pos)
        if n <= 0:
            return b""
        if not (self._cache_start <= self.pos and self.pos + n <= self._cache_start + len(self._cache)):
            end = min(self.size, self.pos + max(n, self.block)) - 1
            self._cache = fetch_range(self.url, self.pos, end, opener=self.opener, sleep=self.sleep)
            self._cache_start = self.pos
            self.requests += 1
        off = self.pos - self._cache_start
        out = self._cache[off:off + n]
        self.pos += len(out)
        return out

    def close(self) -> None:
        self._cache = b""


# ----------------------------------------------------------------------------------- archives


def safe_member_path(dest: Path, name: str) -> Path:
    """Where zip member ``name`` may be written under ``dest``; refuses absolute paths, drive letters and ``..``."""
    p = PurePosixPath(name.replace("\\", "/"))
    if not p.parts or p.is_absolute() or ".." in p.parts or ":" in p.parts[0]:
        raise DataError(f"unsafe member path in archive: {name!r}")
    return dest.joinpath(*p.parts)  # lexical check above suffices: dest is a fresh dir we create, without links


def _is_regular(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return not info.is_dir() and mode != 0o120000  # no directories, no symlinks


ZIP_LOCAL_HEADER = "<4s2B4HL2L2H"  # = zipfile.structFileHeader (APPNOTE 4.3.7)


def read_remote_member(url: str, size: int, info: zipfile.ZipInfo, *, opener: Opener = urllib.request.urlopen,
                       sleep: Callable[[float], None] = time.sleep) -> bytes:
    """One zip member via 1-2 Range requests (local header + data); CRC-checked. Stored/deflate only."""
    guess = 30 + len(info.filename.encode()) + 512
    end = min(size - 1, info.header_offset + guess + info.compress_size - 1)
    data = fetch_range(url, info.header_offset, end, opener=opener, sleep=sleep)
    fh = struct.unpack(ZIP_LOCAL_HEADER, data[:30])
    if fh[0] != b"PK":
        raise DataError(f"{info.filename}: bad local header")
    start = 30 + fh[10] + fh[11]  # file name length, extra field length
    if len(data) < start + info.compress_size:
        data += fetch_range(url, info.header_offset + len(data), info.header_offset + start + info.compress_size - 1,
                            opener=opener, sleep=sleep)
    raw = data[start:start + info.compress_size]
    if info.compress_type == zipfile.ZIP_STORED:
        out = raw
    elif info.compress_type == zipfile.ZIP_DEFLATED:
        out = zlib.decompress(raw, -15)
    else:
        raise DataError(f"{info.filename}: unsupported compression {info.compress_type}")
    if zlib.crc32(out) != info.CRC:
        raise DataError(f"{info.filename}: CRC mismatch")
    return out


class Archive:
    """Uniform read access to the Ahmed data: an extracted folder, a local zip, or the remote Zenodo zip."""

    def __init__(self, kind: str, names: list[str], reader: Callable[[str], bytes], *, root: Path | None = None,
                 parallel: bool = False, close: Callable[[], None] | None = None) -> None:
        self.kind, self.names, self._read, self.root, self.parallel = kind, names, reader, root, parallel
        self._close = close

    def close(self) -> None:
        if self._close is not None:
            self._close()

    @classmethod
    def from_dir(cls, root: Path) -> Archive:
        names = sorted(p.relative_to(root).as_posix() for sub in (AHMED_IMAGES, AHMED_LABELS) if (root / sub).is_dir()
                       for p in (root / sub).rglob("*") if p.is_file())
        return cls("dir", names, lambda n: (root / n).read_bytes(), root=root)

    @classmethod
    def from_zip(cls, path: Path) -> Archive:
        zf = zipfile.ZipFile(path)
        infos = {i.filename: i for i in zf.infolist() if _is_regular(i)}
        lock = threading.Lock()

        def read(n: str) -> bytes:
            with lock:
                return zf.read(infos[n])

        return cls("zip", sorted(infos), read, close=zf.close)

    @classmethod
    def from_url(cls, url: str, size: int, *, opener: Opener = urllib.request.urlopen,
                 sleep: Callable[[float], None] = time.sleep) -> Archive:
        rf = RemoteFile(url, size, opener=opener, sleep=sleep)
        with zipfile.ZipFile(rf) as zf:  # duck-typed seekable file (read/seek/tell)
            infos = {i.filename: i for i in zf.infolist() if _is_regular(i)}
        return cls("remote", sorted(infos), lambda n: read_remote_member(url, size, infos[n], opener=opener, sleep=sleep),
                   parallel=True)

    def read(self, name: str) -> bytes:
        return self._read(name)

    def extract(self, names: Iterable[str], dest: Path, *, workers: int = 16, log: Callable[[str], None] = print) -> int:
        """Write ``names`` under ``dest`` (paths validated; existing files of the same size are kept)."""
        todo = [n for n in names if not (dest / n).is_file()]
        for n in todo:
            safe_member_path(dest, n)

        def one(n: str) -> None:
            out = safe_member_path(dest, n)
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_name(out.name + ".tmp")
            tmp.write_bytes(self.read(n))
            tmp.replace(out)

        t0 = time.time()
        if self.parallel and len(todo) > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(one, todo))
        else:
            for k, n in enumerate(todo, 1):
                one(n)
                if k % 1000 == 0:
                    log(f"  extracted {k}/{len(todo)}")
        log(f"extracted {len(todo)} files from {self.kind} in {time.time() - t0:.0f}s -> {dest}")
        return len(todo)


def label_index(names: Iterable[str]) -> dict[str, list[str]]:
    """Image stem -> Darknet label member(s)."""
    idx: dict[str, list[str]] = {}
    for n in names:
        if n.startswith(AHMED_LABELS + "/") and n.endswith(".txt"):
            idx.setdefault(PurePosixPath(n).stem, []).append(n)
    return idx


def label_for(image: str, idx: Mapping[str, Sequence[str]]) -> str | None:
    """Same pairing rule as convert_annotations.find_label: same relative path first, then a unique stem."""
    rel = PurePosixPath(image).relative_to(AHMED_IMAGES).with_suffix(".txt")
    direct = f"{AHMED_LABELS}/{rel.as_posix()}"
    cands = idx.get(PurePosixPath(image).stem, [])
    if direct in cands:
        return direct
    return cands[0] if len(cands) == 1 else None


def smoke_pick(split_of: Mapping[str, str], has_boxes: Callable[[str], bool], *, n: Mapping[str, int] = SMOKE_N,
               seed: int = 20261008, positive_share: float = 0.75) -> dict[str, list[str]]:
    """Deterministic tiny subset per split (same split assignment as the full run): mostly images with boxes."""
    out: dict[str, list[str]] = {}
    for split, k in n.items():
        cands = sorted((img for img, s in split_of.items() if s == split),
                       key=lambda img: hashlib.sha256(f"{seed}:{img}".encode()).hexdigest())
        pos: list[str] = []
        neg: list[str] = []
        want_pos = round(k * positive_share)
        for img in cands[: max(k * 8, 40)]:
            if len(pos) >= want_pos and len(pos) + len(neg) >= k:
                break
            (pos if has_boxes(img) else neg).append(img)
        chosen = pos[:want_pos]
        chosen += neg[: k - len(chosen)]
        chosen += [p for p in pos[want_pos:] if p not in chosen][: k - len(chosen)]
        if not chosen:
            raise DataError(f"split {split!r} has no images")
        out[split] = sorted(chosen)
    return out


def subset_splits(splits: bytes, images: Iterable[str], out_dir: Path) -> dict[str, Any]:
    """Filtered copy of a committed splits.json (+ a manifest with the new sha256) for SMOKE_TEST."""
    data = json.loads(splits.decode("utf-8"))
    keep = set(images)
    missing = keep - set(data["images"])
    if missing:
        raise DataError(f"{len(missing)} images not in the committed splits.json: {sorted(missing)[:3]}")
    img_groups = {k: v for k, v in (data.get("image_groups") or {}).items() if k in keep}
    sub = {**data, "images": {k: v for k, v in data["images"].items() if k in keep}, "image_groups": img_groups,
           "groups": {g: s for g, s in (data.get("groups") or {}).items() if g in set(img_groups.values())}}
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "splits.json"
    out.write_text(json.dumps(sub, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    counts: dict[str, int] = {}
    for s in sub["images"].values():
        counts[s] = counts.get(s, 0) + 1
    manifest = {"splits_file": "splits.json", "splits_sha256": sha256_file(out), "smoke_subset_of": hashlib.sha256(splits).hexdigest(),
                "group_quality": "smoke_subset", "image_counts": counts,
                "limitations": ["SMOKE_TEST subset: proves the pipeline runs; metrics are meaningless."]}
    (out_dir / "split_manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return manifest


def committed_splits(src_ml: Path, dataset: str = "ahmed2025") -> tuple[bytes, Path] | None:
    """(splits.json bytes, split_manifest.json) committed under ml/data/splits/<dataset>/, verified against each other.
    A CRLF copy (git autocrlf on Windows) is accepted when its LF form matches -- the bytes returned are the LF form."""
    d = src_ml / "data" / "splits" / dataset
    s, m = d / "splits.json", d / "split_manifest.json"
    if not (s.is_file() and m.is_file()):
        return None
    want = json.loads(m.read_text(encoding="utf-8")).get("splits_sha256")
    raw = s.read_bytes()
    for candidate in (raw, raw.replace(b"\r\n", b"\n")):
        if hashlib.sha256(candidate).hexdigest() == want:
            return candidate, m
    raise DataError(f"committed {s} sha256 != {m} ({want}); the repo copy is damaged")


def canonical_images(canonical: Path) -> list[str]:
    return [json.loads(line)["image"] for line in canonical.read_text(encoding="utf-8").splitlines() if line.strip()]


def box_counts(canonical: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in canonical.read_text(encoding="utf-8").splitlines():
        for b in json.loads(line).get("boxes", []) if line.strip() else []:
            counts[b["cls"]] = counts.get(b["cls"], 0) + 1
    return counts


def locate_ahmed(inputs: Sequence[Path], *, size: int = AHMED_SIZE) -> tuple[str, Path] | None:
    """An attached copy: ('dir', root containing Dataset/Images) or ('zip', path to Dataset.zip of the right size)."""
    root = find_dir(inputs, AHMED_MARKER)
    if root is not None:
        return "dir", root
    for z in find_named(inputs, AHMED_ZIP, want_dir=False, max_depth=4):
        if z.stat().st_size == size:
            return "zip", z
    return None


def prepare_ahmed(*, inputs: Sequence[Path], tmp: Path, smoke: bool, split_of: Mapping[str, str],
                  urls: Sequence[str] = AHMED_URLS, size: int = AHMED_SIZE, md5: str = AHMED_MD5,
                  opener: Opener = urllib.request.urlopen, sleep: Callable[[float], None] = time.sleep,
                  free: Callable[[Path], float] = free_gb, log: Callable[[str], None] = print) -> dict[str, Any]:
    """Make the Ahmed 2025 photos + Darknet labels available on local disk (public CC BY 4.0 data only).

    Full run: attached folder as-is, else extract Images + Darknet labels from an attached/downloaded Dataset.zip
    (MD5-verified) into ``tmp/ahmed2025/extracted``. SMOKE_TEST: a tiny deterministic subset (``SMOKE_N``) is
    copied to ``tmp/ahmed2025_smoke`` -- from the remote zip by HTTP Range requests when nothing is attached
    (a few MB instead of 1.6 GB). Returns {"root", "source", "images" (set for smoke, None for full)}."""
    urls = tuple(u for u in (os.environ.get("DENTISAI_ZENODO_URL"),) if u) + tuple(urls)
    found = locate_ahmed(inputs, size=size)
    log("Dataset: Ahmed et al. 2025 (Zenodo 14827784, CC BY 4.0). " + AHMED_ATTRIBUTION)
    archive: Archive | None = None
    source = ""
    if found is not None and found[0] == "dir":
        if not smoke:
            log(f"data: attached folder {found[1]}")
            return {"root": found[1], "source": "attached_dir", "images": None}
        archive, source = Archive.from_dir(found[1]), "attached_dir"
    elif found is not None:
        archive, source = Archive.from_zip(found[1]), "attached_zip"
    elif smoke:
        url = pick_url(urls, size, opener=opener, sleep=sleep)
        log(f"data: SMOKE_TEST -> reading only the needed files from {url} (HTTP Range, no full download)")
        archive, source = Archive.from_url(url, size, opener=opener, sleep=sleep), "zenodo_range"
    else:
        need = size * 2 / 2**30 + 1.0
        if free(tmp) < need:
            raise DataError(f"need >= {need:.1f} GB free in {tmp} for Dataset.zip + extraction, have {free(tmp):.1f} GB")
        dl = tmp / "download"
        z = download_parallel(urls, dl / AHMED_ZIP, size=size, md5=md5, opener=opener, sleep=sleep, log=log)
        archive, source = Archive.from_zip(z), "zenodo_download"
    if smoke:
        dest = tmp / "ahmed2025_smoke"
        idx = label_index(archive.names)
        cache: dict[str, bool] = {}
        arc = archive

        def has_boxes(img: str) -> bool:  # no label file = negative, without a network request
            if img not in cache:
                lab = label_for(img, idx)
                cache[img] = lab is not None and bool(arc.read(lab).strip())
            return cache[img]

        picked = smoke_pick(split_of, has_boxes)
        images = sorted(i for v in picked.values() for i in v)
        present = set(archive.names)
        missing = [i for i in images if i not in present]
        if missing:
            raise DataError(f"images listed in splits.json are not in the dataset: {missing[:3]}")
        labels = [lab for i in images if (lab := label_for(i, idx)) is not None]
        archive.extract(images + labels, dest, log=log)
        archive.close()
        log(f"data: SMOKE subset {({k: len(v) for k, v in picked.items()})}, with boxes: {sum(cache.get(i, False) for i in images)}")
        return {"root": dest, "source": source, "images": set(images)}
    dest = tmp / "ahmed2025" / "extracted"
    wanted = [n for n in archive.names if n.startswith((AHMED_IMAGES + "/", AHMED_LABELS + "/"))]
    archive.extract(wanted, dest, log=log)
    archive.close()
    if source == "zenodo_download" and os.environ.get("DENTISAI_KEEP_ZIP") != "1":
        (tmp / "download" / AHMED_ZIP).unlink(missing_ok=True)  # saves 1.6 GB; .md5ok stays as a record
    return {"root": dest, "source": source, "images": None}


def build_processed(*, src_ml: Path, data_root: Path, processed: Path, images: set[str] | None,
                    python: str = sys.executable, run: Callable[..., int] | None = None) -> dict[str, Any]:
    """canonical.jsonl from the YOLO labels (convert_annotations, class map of ADR 014) + the COMMITTED split
    (ml/data/splits/ahmed2025, sha256-verified) -- or its SMOKE_TEST subset. Regenerates the split only if the
    committed one is missing (same commands as ml/data/scripts/fetch_ahmed2025.md; full data needed)."""
    runner = run or sh
    det = src_ml / "detection"
    processed.mkdir(parents=True, exist_ok=True)
    p = processed_paths(processed)
    probs = class_map_problems(src_ml / "data" / "class_maps" / "ahmed2025.yaml")
    if any("null" in x for x in probs):
        raise DataError("fill ml/data/class_maps/ahmed2025.yaml first (data engineer + drg): " + "; ".join(probs))
    runner([python, "convert_annotations.py", "to-canonical", "--in-format", "yolo", "--root", data_root,
            "--images", AHMED_IMAGES, "--labels", AHMED_LABELS, "--class-map", src_ml / "data/class_maps/ahmed2025.yaml",
            "--source", "ahmed2025", "--missing-label", "negative", "--on-invalid", "drop", "--out", p["canonical"]], cwd=det)
    committed = committed_splits(src_ml)
    if committed is not None and images is not None:
        subset_splits(committed[0], images, p["splits"].parent)
    elif committed is not None:
        p["splits"].parent.mkdir(parents=True, exist_ok=True)
        p["splits"].write_bytes(committed[0])
        shutil.copy2(committed[1], p["manifest"])
    elif images is None:
        work = processed / "_groups"
        runner([python, src_ml / "data/scripts/ahmed2025_groups.py", "--root", data_root, "--out-dir", work], cwd=det)
        runner([python, src_ml / "data/scripts/make_splits.py", "--canonical", work / "split_input.jsonl", "--out-dir",
                p["splits"].parent, "--group-mode", "session_csv", "--metadata-csv", work / "groups.csv", "--seed", "20261008"], cwd=det)
    else:
        raise DataError("SMOKE_TEST needs the committed ml/data/splits/ahmed2025/splits.json (missing from the code)")
    info = check_processed(processed)
    assert info is not None
    in_canon = set(canonical_images(p["canonical"]))
    in_splits = set(json.loads(p["splits"].read_text(encoding="utf-8"))["images"])
    if in_canon != in_splits:
        extra, gone = sorted(in_canon - in_splits), sorted(in_splits - in_canon)
        raise DataError(f"canonical.jsonl and splits.json disagree: {len(extra)} images without a split (e.g. {extra[:2]}), "
                        f"{len(gone)} split images missing from the data (e.g. {gone[:2]}). Wrong/partial dataset?")
    info["split_source"] = "committed" if committed is not None else "regenerated"
    info["boxes_per_class"] = box_counts(p["canonical"])
    info["n_images"] = len(in_canon)
    return info


def mini_layout(canonical: Path, splits: Path, images_root: Path, out: Path, *, layout: str, n: Mapping[str, int],
                max_side: int = 640, seed: int = 20261008) -> dict[str, int]:
    """Tiny trainer layout (EXIF-free resized copies under ``out/images``) for a minutes-long toolchain check
    before the long run. Positives first, deterministic. ``out`` must be scratch (never exported)."""
    from canonical import read_jsonl
    from convert_annotations import load_split_assignments, partition, resized_copy, write_coco_layout

    parts = partition(read_jsonl(canonical), load_split_assignments(splits))
    counts = {}
    for split, k in n.items():
        recs = sorted(parts.get(split, []), key=lambda r: (not r.boxes, hashlib.sha256(f"{seed}:{r.image}".encode()).hexdigest()))[:k]
        if not recs:
            raise DataError(f"no {split} images for the mini layout")
        small = resized_copy(recs, images_root, out / "images", max_side=max_side)
        write_coco_layout(small, out / "data", layout=layout, split=split, images_root=out / "images")  # type: ignore[arg-type]
        counts[split] = len(small)
    return counts


# ------------------------------------------------------------------------------------ processes


FOREIGN_PY_VARS = ("PYTHONPATH", "PYTHONHOME")


def child_env(exe: str, extra: Mapping[str, str] | None = None, environ: Mapping[str, str] | None = None,
              kernel: str = sys.executable) -> dict[str, str]:
    """Environment for a child process. Programs that are NOT the kernel's interpreter (venv pythons, onnx2tf, git)
    get no PYTHONPATH/PYTHONHOME: the kernel's paths (YOLOX checkout, Kaggle extras) would shadow a venv's own
    numpy etc. -- found in the deep simulation (py3.11 numpy leaking into the py3.12 export venv)."""
    env = dict(os.environ if environ is None else environ)
    same = os.path.normcase(os.path.abspath(exe)) == os.path.normcase(os.path.abspath(kernel))
    if not same:
        for k in FOREIGN_PY_VARS:
            env.pop(k, None)
        bindir = os.path.dirname(os.path.abspath(exe))
        if os.path.isfile(os.path.join(bindir, "activate")) or os.path.isfile(os.path.join(bindir, "pyvenv.cfg")) \
                or os.path.isfile(os.path.join(os.path.dirname(bindir), "pyvenv.cfg")):
            # a venv tool (e.g. onnx2tf) calls sibling CLIs such as `onnxsim` by name -> the venv bin must be on PATH
            # (deep simulation: "No such file or directory: 'onnxsim'" -> unsimplified graph -> INT8 failed on EQUAL)
            env["PATH"] = os.pathsep.join([bindir, *filter(None, [env.get("PATH")])])
    env.update(extra or {})
    return env


def sh(cmd: Sequence[Any] | str, *, cwd: Path | None = None, env: Mapping[str, str] | None = None, check: bool = True,
       timeout: float | None = None) -> int:  # pragma: no cover - thin subprocess wrapper
    args = shlex.split(cmd) if isinstance(cmd, str) else [str(c) for c in cmd]
    print("$", " ".join(shlex.quote(a) for a in args), flush=True)
    t0 = time.time()
    res = subprocess.run(args, cwd=cwd, env=child_env(args[0], env), check=False, timeout=timeout)
    print(f"[exit {res.returncode} in {time.time() - t0:.0f}s]", flush=True)
    if check and res.returncode != 0:
        raise RuntimeError(f"command failed ({res.returncode}): {args[0]} ... (see the output above)")
    return res.returncode


def missing_modules(mapping: Mapping[str, str], find_spec: Callable[[str], Any] = importlib.util.find_spec) -> list[str]:
    """pip specs for the import names that are NOT importable in this kernel (so preinstalled torch/cv2 stay)."""
    out = []
    for mod, spec in mapping.items():
        try:
            ok = find_spec(mod) is not None
        except (ImportError, ValueError):
            ok = False
        if not ok:
            out.append(spec)
    return out


def uv_command(*, which: Callable[[str], str | None] = shutil.which, run: Callable[..., int] | None = None) -> list[str]:
    """``uv`` from PATH, else ``python -m uv`` after a pinned pip install."""
    found = which("uv")
    if found:
        return [found]
    (run or sh)([sys.executable, "-m", "pip", "install", "-q", f"uv=={UV_VERSION}"])
    return [sys.executable, "-m", "uv"]


def venv_python(path: Path) -> Path:
    return path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def python_version_of(py: Path, run: Callable[..., Any] = subprocess.run) -> str:
    res = run([str(py), "-c", "import sys; print('%d.%d' % sys.version_info[:2])"], capture_output=True, text=True, check=False)
    return (res.stdout or "").strip()


def create_venv(path: Path, python: str, *, uv: Sequence[str], run: Callable[..., int] | None = None,
                which: Callable[[str], str | None] = shutil.which,
                version_of: Callable[[Path], str] = python_version_of) -> Path:
    """Python ``python`` venv at ``path``: (1) uv (finds or DOWNLOADS a standalone CPython), (2) conda, else stop
    with an explanation. Returns the interpreter; reuses an existing one of the right version."""
    runner = run or sh
    py = venv_python(path)
    if py.exists() and version_of(py) == python:
        return py
    if path.exists():
        shutil.rmtree(path)
    attempts: list[str] = []
    if runner([*uv, "venv", "--python", python, "--python-preference", "managed", str(path)], check=False) == 0 and version_of(py) == python:
        return py
    attempts.append("uv venv (managed CPython download)")
    shutil.rmtree(path, ignore_errors=True)
    conda = which("conda") or which("mamba")
    if conda and runner([conda, "create", "-y", "-q", "-p", str(path), f"python={python}"], check=False) == 0 and version_of(py) == python:
        return py
    attempts.append("conda create" if conda else "conda (not installed)")
    raise RuntimeError(f"could not create a Python {python} environment at {path}; tried: {', '.join(attempts)}. "
                       "Check that Internet is On (uv downloads Python from github.com) and report the log above.")


def ensure_venv(path: Path, python: str, lock: Path | Sequence[str], *, uv: Sequence[str] | None = None,
                run: Callable[..., int] | None = None, **kw: Any) -> Path:
    """Venv with the EXACT pins of ``lock`` (a requirements file compiled for Linux x86_64) or a package list."""
    runner = run or sh
    uv_cmd = list(uv) if uv is not None else uv_command(run=runner)
    py = create_venv(path, python, uv=uv_cmd, run=runner, **kw)
    reqs = ["-r", str(lock)] if isinstance(lock, Path) else list(lock)
    runner([*uv_cmd, "pip", "install", "--python", str(py), *reqs])
    return py


def nvidia_lib_dirs(venv: Path) -> list[str]:
    """CUDA libraries shipped as pip wheels inside a venv (``tensorflow[and-cuda]``), for LD_LIBRARY_PATH."""
    return sorted(str(p) for p in venv.glob("lib/python3*/site-packages/nvidia/*/lib") if p.is_dir())


TF_GPU_PROBE = "import tensorflow as tf; print('TF', tf.__version__); print('GPUS=%d' % len(tf.config.list_physical_devices('GPU')))"


def tf_gpu_count(py: Path, *, env: Mapping[str, str] | None = None, run: Callable[..., Any] = subprocess.run) -> int:
    """GPUs TensorFlow sees inside a venv (-1 if TensorFlow does not even import)."""
    res = run([str(py), "-c", TF_GPU_PROBE], capture_output=True, text=True, check=False, timeout=600,
              env=None if env is None else {**os.environ, **env})
    text = (res.stdout or "") + (res.stderr or "")
    print(text.strip()[-1500:])
    for line in (res.stdout or "").splitlines():
        if line.startswith("GPUS="):
            return int(line.split("=", 1)[1])
    return -1


def ensure_tf_gpu(py: Path, venv: Path, *, need: bool, run: Callable[..., Any] = subprocess.run,
                  environ: dict[str, str] | None = None) -> int:
    """Make TF 2.15 in the venv see the GPU: plain first, then with the venv's pip CUDA libs on LD_LIBRARY_PATH
    (exported to ``environ`` -- default ``os.environ`` -- so the Model Maker subprocess inherits it)."""
    target = os.environ if environ is None else environ
    n = tf_gpu_count(py, run=run)
    if n > 0 or n < 0:
        if n < 0:
            raise RuntimeError("TensorFlow does not import in the Model Maker venv -- see the error above")
        return n
    libs = nvidia_lib_dirs(venv)
    if libs:
        ld = os.pathsep.join([*libs, *filter(None, [target.get("LD_LIBRARY_PATH")])])
        n = tf_gpu_count(py, env={"LD_LIBRARY_PATH": ld}, run=run)
        if n > 0:
            target["LD_LIBRARY_PATH"] = ld
            print(f"TF sees {n} GPU(s) after adding {len(libs)} pip CUDA lib dirs to LD_LIBRARY_PATH")
            return n
    if need:
        raise RuntimeError("TensorFlow 2.15 in the Model Maker venv sees NO GPU although one is attached "
                           "(check Accelerator = GPU T4 x2; report the TF log above)")
    return 0


def latest_run(runs: Path, candidate: str) -> Path:
    """Newest ``run.json`` of a real (non dry-run) training run of ``candidate`` (run ids start with a UTC timestamp)."""
    found = [p for p in sorted(runs.glob(f"*_{candidate}_*/run.json"))
             if json.loads(p.read_text(encoding="utf-8")).get("status") != "dry_run"]
    if not found:
        raise FileNotFoundError(f"no training run of {candidate} under {runs}")
    return found[-1]


def remaining_hours(total_h: float, started: float, reserve_h: float, *, now: Callable[[], float] = time.time,
                    floor_h: float = 0.25) -> float:
    """Training budget left in this session: total minus time already used minus a reserve for export/eval."""
    return max(floor_h, round(total_h - (now() - started) / 3600 - reserve_h, 3))


def forbidden_outputs(root: Path) -> list[str]:
    """Files under the persisted outputs that must never be there: photos / image-derived arrays (by name or magic)."""
    bad = []
    magic = (b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"BM", b"\x93NUMPY")
    for p in sorted(root.rglob("*")) if root.is_dir() else []:
        if not p.is_file():
            continue
        if p.suffix.lower() in (*IMAGE_EXT, ".npy", ".npz", ".gif", ".tif", ".tiff", ".heic"):
            bad.append(str(p))
            continue
        with p.open("rb") as fh:
            if fh.read(8).startswith(magic):
                bad.append(str(p))
    return bad


def describe_outputs(root: Path, highlight: str = "download_") -> list[str]:
    lines = []
    for p in sorted(root.rglob("*")) if root.is_dir() else []:
        if p.is_file():
            mark = "  <-- UNDUH INI" if p.name.startswith(highlight) and p.suffix == ".zip" else ""
            lines.append(f"{p.stat().st_size / 2**20:9.2f} MiB  {p.relative_to(root).as_posix()}{mark}")
    return lines


def gpu_summary() -> str:  # pragma: no cover - needs nvidia-smi
    try:
        return subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                              capture_output=True, text=True, check=False, timeout=30).stdout.strip() or "no GPU"
    except (OSError, subprocess.TimeoutExpired):
        return "no GPU (nvidia-smi missing)"


def env_record(extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {"compute_env": compute_env(), "python": sys.version.split()[0], "platform": sys.platform, "gpu": gpu_summary(),
            "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **(extra or {})}
