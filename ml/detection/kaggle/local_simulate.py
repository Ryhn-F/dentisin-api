"""Run the generated Kaggle notebooks (``*.ipynb``) locally against a FAKE Kaggle layout -- no Kaggle, no GPU.

What is real: the notebook cells themselves (the generated .ipynb, incl. the inlined bootstrap), git clone through
the token path (from a local git repo; a fake Kaggle Secret), the Ahmed 2025 data (the REAL local Dataset.zip /
extracted tree, served to the notebook by a local HTTP server with Range support, or "attached" by link), the
committed split, conversion, config + licence gate, trainer layouts, and the final output guard.

What is replaced (``--level local``, any OS): install cells and GPU training are skipped (stub values), and the
trainer layouts that the training adapters would write are written + checked against what the libraries read
(MediaPipe Model Maker 0.2.1.4 ``Dataset.from_coco_folder``; YOLOX ``COCODataset``).

``--level deep`` (Linux, e.g. ``docker run python:3.11-slim``) also runs the install cells for real (uv venvs from the
pinned locks; CUDA wheels filtered out with ``--cpu-locks``) and every later cell. YOLOX training (CUDA-only
``tools/train.py``) is replaced by one CPU iteration of the real YOLOX model on the real layout + a YOLOX-format
checkpoint; Model Maker trains for real on CPU.

    uv run python kaggle/local_simulate.py --notebook yolox --sim-dir D:/tmp/sim --data remote --code git
    python kaggle/local_simulate.py --notebook mediapipe --level deep --cpu-locks ...   # inside a Linux container

Data never leaves the machine: the HTTP server binds 127.0.0.1 only.
"""

from __future__ import annotations

import argparse
import contextlib
import http.server
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import types
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
DET = HERE.parent
ML = DET.parent
BACKEND = ML.parent
sys.path.insert(0, str(HERE))
import bootstrap  # noqa: E402

FAKE_TOKEN = "github_pat_SIMULATION_ONLY_0123456789abcdef"
NOTEBOOKS = {"yolox": "yolox_nano_kaggle.ipynb", "mediapipe": "mediapipe_ssd_kaggle.ipynb"}


# ------------------------------------------------------------------------------------ fake network


def range_handler(path: Path) -> type[http.server.BaseHTTPRequestHandler]:
    size = path.stat().st_size

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a: Any) -> None:
            pass

        def do_GET(self) -> None:
            m = re.fullmatch(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
            if m is None:
                start, end, code = 0, size - 1, 200
            else:
                start, end, code = int(m.group(1)), int(m.group(2) or size - 1), 206
                end = min(end, size - 1)
            self.send_response(code)
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            if code == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with path.open("rb") as fh:
                fh.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = fh.read(min(left, 1 << 20))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)

    return H


@contextlib.contextmanager
def serve_file(path: Path) -> Iterator[str]:
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), range_handler(path))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/{path.name}"
    finally:
        srv.shutdown()


# ------------------------------------------------------------------------------------ fake Kaggle


def make_git_source(dest: Path) -> str:
    """A local git repo holding exactly what ``artifacts.py pack-code`` ships (= the WORKING TREE, so uncommitted
    changes are tested). Returned as a file:// URL for DENTISAI_GIT_URL."""
    sys.path.insert(0, str(DET))
    import artifacts

    bootstrap.rmtree(dest)
    for src, rel in artifacts.code_files(ML):
        out = dest / "ml" / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, out)
    git = ["git", "-C", str(dest), "-c", "user.name=sim", "-c", "user.email=sim@localhost", "-c", "commit.gpgsign=false",
           "-c", "core.autocrlf=false", "-c", "core.safecrlf=false"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(dest)], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "simulation snapshot"], check=True)
    return dest.resolve().as_uri()


def make_code_dataset(inputs: Path) -> Path:
    """The private Kaggle dataset route: pack-code zip, extracted like Kaggle does on upload."""
    sys.path.insert(0, str(DET))
    import artifacts

    zpath = inputs.parent / "dentisai-ml-code.zip"
    artifacts.pack_code(zpath)
    out = inputs / "dentisai-ml-code"
    with zipfile.ZipFile(zpath) as zf:
        zf.extractall(out)
    return out


def install_fake_secret(token: str) -> None:
    mod = types.ModuleType("kaggle_secrets")

    class UserSecretsClient:
        def get_secret(self, name: str) -> str:
            if name != "GITHUB_TOKEN":
                raise KeyError(name)
            return token

    mod.UserSecretsClient = UserSecretsClient  # type: ignore[attr-defined]
    sys.modules["kaggle_secrets"] = mod


def link_dir(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(src), str(dst))  # no admin rights needed
    else:
        dst.symlink_to(src, target_is_directory=True)


def attach_data(mode: str, inputs: Path, zip_path: Path, extracted: Path) -> None:
    if mode == "dir":
        link_dir(extracted, inputs / "ahmed2025-caries")
    elif mode == "zip":
        d = inputs / "ahmed2025-caries"
        d.mkdir(parents=True, exist_ok=True)
        os.link(zip_path, d / zip_path.name)  # hard link: same file, no copy, never written


# ---------------------------------------------------------------------------------------- cells


def load_cells(ipynb: Path) -> list[tuple[int, str]]:
    nb = json.loads(ipynb.read_text(encoding="utf-8"))
    out = []
    for c in nb["cells"]:
        if c["cell_type"] != "code":
            continue
        src = "".join(c["source"])
        m = re.match(r"# (\d+)\.", src)
        if m is None:
            raise ValueError(f"code cell without a '# N.' title: {src[:60]!r}")
        out.append((int(m.group(1)), src))
    return out


def patch_settings(src: str, smoke: bool) -> str:
    new, n = re.subn(r"^SMOKE_TEST = (True|False)", f"SMOKE_TEST = {smoke}", src, flags=re.M)
    if n != 1:
        raise ValueError("settings cell has no SMOKE_TEST line")
    return new


class Tee(io.TextIOBase):
    def __init__(self, *streams: Any) -> None:
        self.streams = streams

    def write(self, s: str) -> int:
        for st in self.streams:
            st.write(s)
        return len(s)

    def flush(self) -> None:
        for st in self.streams:
            st.flush()


def run_cell(num: int, src: str, ns: dict[str, Any], log: io.StringIO) -> tuple[bool, str]:
    print(f"\n{'#' * 30} cell {num} {'#' * 30}", flush=True)
    t0 = time.time()
    try:
        with contextlib.redirect_stdout(Tee(sys.__stdout__, log)):
            exec(compile(src, f"<cell {num}>", "exec"), ns)
    except SystemExit as exc:
        return False, f"SystemExit: {str(exc)[:300]}"
    except Exception:
        return False, traceback.format_exc()[-3000:]
    return True, f"{time.time() - t0:.0f}s"


# ------------------------------------------------------------------------------------- checks


def check_token_not_leaked(tmp: Path, captured: str, token: str) -> list[str]:
    import base64

    needles = [token, base64.b64encode(f"x-access-token:{token}".encode()).decode()]
    problems = [f"token printed in cell output ({n[:12]}...)" for n in needles if n in captured]
    git_dir = tmp / "src" / ".git"
    for p in git_dir.rglob("*") if git_dir.is_dir() else []:
        if p.is_file() and any(n.encode() in p.read_bytes() for n in needles):
            problems.append(f"token stored in {p}")
    return problems


def check_mediapipe_layout(split_dir: Path, *, min_annotated: int) -> list[str]:
    """What mediapipe-model-maker 0.2.1.4 Dataset.from_coco_folder reads (dataset_util.py, read from the wheel):
    <dir>/images/<file_name> (decoded as JPEG), <dir>/labels.json; category id 0 only as 'background'; images
    WITHOUT annotations are skipped by Model Maker -> a split needs >= batch_size annotated images."""
    from PIL import Image

    probs: list[str] = []
    labels = split_dir / "labels.json"
    if not labels.is_file() or not (split_dir / "images").is_dir():
        return [f"{split_dir}: needs images/ + labels.json"]
    data = json.loads(labels.read_text(encoding="utf-8"))
    cats = {int(c["id"]): c["name"] for c in data["categories"]}
    if cats.get(0, "background") != "background":
        probs.append(f"{labels}: category id 0 must be 'background'")
    if sorted(cats.values()) != ["caries_permanent", "caries_primary"]:
        probs.append(f"{labels}: unexpected categories {cats}")
    sizes = {}
    for im in data["images"]:
        f = split_dir / "images" / im["file_name"]
        if not f.is_file():
            probs.append(f"missing image {f}")
            continue
        with Image.open(f) as pil:
            if pil.format != "JPEG":
                probs.append(f"{f}: {pil.format}, Model Maker decodes JPEG only")
            sizes[im["id"]] = pil.size
            if pil.size != (im["width"], im["height"]):
                probs.append(f"{f}: size {pil.size} != labels.json {(im['width'], im['height'])}")
    annotated = set()
    for a in data["annotations"]:
        x, y, w, h = a["bbox"]
        W, H = sizes.get(a["image_id"], (0, 0))
        if a["category_id"] not in cats or a["category_id"] == 0:
            probs.append(f"annotation {a['id']}: category {a['category_id']}")
        if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > W + 1e-3 or y + h > H + 1e-3:
            probs.append(f"annotation {a['id']}: bbox {a['bbox']} outside {W}x{H}")
        annotated.add(a["image_id"])
    if len(annotated) < min_annotated:
        probs.append(f"{split_dir}: only {len(annotated)} annotated images (< batch size {min_annotated}; Model Maker skips negatives)")
    extra = {p.name for p in (split_dir / "images").iterdir()} - {i["file_name"] for i in data["images"]}
    if extra:
        probs.append(f"{split_dir}/images has {len(extra)} files not in labels.json")
    return probs


def check_yolox_layout(data_dir: Path, split: str) -> list[str]:
    """YOLOX COCODataset(data_dir, json_file=instances_<split>.json, name=<split>): <data_dir>/annotations/<json> +
    <data_dir>/<split>/<file_name>; contiguous class ids come from sorted category ids."""
    ann = data_dir / "annotations" / f"instances_{split}.json"
    if not ann.is_file():
        return [f"missing {ann}"]
    data = json.loads(ann.read_text(encoding="utf-8"))
    probs = [f"missing {data_dir / split / i['file_name']}" for i in data["images"] if not (data_dir / split / i["file_name"]).is_file()]
    if [c["id"] for c in data["categories"]] != [1, 2]:
        probs.append(f"{ann}: category ids {[c['id'] for c in data['categories']]}")
    return probs


def write_and_check_layouts(ns: dict[str, Any], work: Path) -> list[str]:
    """Exactly the calls the training adapters make (partition -> resized_copy -> write_coco_layout)."""
    from canonical import read_jsonl
    from convert_annotations import load_split_assignments, partition, resized_copy, write_coco_layout

    P, root, cfg = ns["P"], ns["DATA_ROOT"], ns["CFG"]
    parts = partition(read_jsonl(P["canonical"]), load_split_assignments(P["splits"]))
    probs: list[str] = []
    batch = int(cfg["train"]["batch_size"])
    for layout, max_side in (("mediapipe", 640), ("yolox", 832)):
        out = work / f"layout_{layout}"
        for split in ("train", "val"):
            recs = sorted(parts[split], key=lambda r: not r.boxes)[:24]  # a sample is enough to check the format
            small = resized_copy(recs, root, work / f"images_{max_side}", max_side=max_side)
            write_coco_layout(small, out, layout=layout, split=split, images_root=work / f"images_{max_side}", link=True)  # type: ignore[arg-type]
            if layout == "mediapipe":
                probs += check_mediapipe_layout(out / split, min_annotated=min(batch, 4) if split == "val" else batch)
            else:
                probs += check_yolox_layout(out, split)
        print(f"layout {layout}: {out} ({'ok' if not probs else 'PROBLEMS'})")
    return probs


# ------------------------------------------------------------------------------------- deep mode


def cpu_lock(src: Path, dest: Path) -> Path:
    """The pinned lock minus CUDA wheels (CPU-only container)."""
    lines = [ln for ln in src.read_text(encoding="utf-8").splitlines() if not ln.startswith("nvidia-")]
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dest


def fake_yolox_train(ns: dict[str, Any]) -> Callable[[], int]:  # pragma: no cover - needs torch + YOLOX (deep mode only)
    """Replaces the CUDA-only YOLOX tools/train.py by ONE real CPU iteration (real Exp, real layout, real model,
    pretrained weights loaded the way YOLOX does) + a checkpoint in YOLOX's format; run.json via train.run()."""

    def go() -> int:
        import numpy as np
        import torch

        import train as train_mod
        from canonical import read_jsonl
        from convert_annotations import load_split_assignments, partition, resized_copy, write_coco_layout
        from trainers import yolox_adapter as ya

        def cpu_train(ctx: Any) -> dict[str, Any]:
            opts = ya.options(ctx)
            work = Path(str(opts["work_dir"]))
            data_dir = work / "data_yolox"
            parts = partition(read_jsonl(ctx.canonical), load_split_assignments(ctx.splits))
            for split in ("train", "val"):
                small = resized_copy(parts[split], ctx.images_root, work / "images", max_side=int(opts["max_side"]))
                write_coco_layout(small, data_dir, layout="yolox", split=split, images_root=work / "images", link=True)
            exp_dir = Path(str(opts["output_dir"])) / ya.EXP_NAME
            exp_dir.mkdir(parents=True, exist_ok=True)
            exp_path = ctx.run_dir / f"{ya.EXP_NAME}.py"
            exp_path.write_text(ya.render_exp(ctx, data_dir, opts), encoding="utf-8")
            from yolox.exp import get_exp
            from yolox.utils import load_ckpt

            exp = get_exp(str(exp_path), None)
            model = exp.get_model()
            if ctx.cfg["model"].get("pretrained"):
                state = torch.load(str(ctx.cfg["model"]["pretrained"]), map_location="cpu")["model"]  # as YOLOX Trainer
                model = load_ckpt(model, state)
            model.train()
            ds = exp.get_dataset(cache=False)
            items = [ds[i] for i in range(min(2, len(ds)))]
            imgs = torch.from_numpy(np.stack([it[0] for it in items])).float()
            targets = torch.from_numpy(np.stack([it[1] for it in items])).float()
            opt = torch.optim.SGD(model.parameters(), lr=1e-4, momentum=0.9)
            out = model(imgs, targets)
            out["total_loss"].backward()
            opt.step()
            print("CPU iteration losses:", {k: float(v) for k, v in out.items() if hasattr(v, "item")})
            ckpt = {"start_epoch": 1, "model": model.state_dict(), "optimizer": opt.state_dict(),
                    "best_ap": np.float64(0.0), "curr_ap": np.float64(0.0)}
            torch.save(ckpt, exp_dir / "latest_ckpt.pth")
            try:
                torch.load(exp_dir / "latest_ckpt.pth", map_location="cpu")
                print("torch.load default on YOLOX-format ckpt: OK")
            except Exception as exc:
                print("torch.load default on YOLOX-format ckpt FAILS without TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD:", type(exc).__name__)
            return {"command": ["<simulated CPU iteration>"], "exp_file": str(exp_path), "status": "completed", "resumed_from": None,
                    "hours": 0.0, "checkpoint_dir": str(exp_dir), "best_ckpt": str(exp_dir / "best_ckpt.pth"),
                    "latest_ckpt": str(exp_dir / "latest_ckpt.pth"), "note": "SIMULATED (local_simulate.py deep mode)"}

        code, _ = train_mod.run(ns["RUN_CFG"], allow_unverified_license=True, runs_dir=ns["RUNS"],
                                adapter_loader=lambda name: types.SimpleNamespace(train=cpu_train))
        return code

    return go


# ------------------------------------------------------------------------------------------ main


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--notebook", choices=sorted(NOTEBOOKS), required=True)
    p.add_argument("--sim-dir", type=Path, required=True, help="fresh scratch dir (use a big disk; NOT inside the repo)")
    p.add_argument("--data", choices=["remote", "zip", "dir"], default="remote",
                   help="remote = nothing attached, served by a local Range HTTP server; zip/dir = 'attached' dataset")
    p.add_argument("--code", choices=["git", "attached"], default="git")
    p.add_argument("--full", action="store_true", help="SMOKE_TEST = False (full data; heavy)")
    p.add_argument("--level", choices=["local", "deep"], default="local")
    p.add_argument("--cpu-locks", action="store_true", help="deep mode: drop nvidia-* wheels from the locks")
    p.add_argument("--no-tf", action="store_true", help="deep mode, YOLOX: export venv WITHOUT TensorFlow (small disks): "
                   "onnx2tf tf_converter is then expected to fail and flatbuffer_direct is used")
    p.add_argument("--min-free-gb", type=float, help="lower the preflight disk threshold (small local disks); reported")
    p.add_argument("--zip", type=Path, default=ML / "data/raw/ahmed2025/download/Dataset.zip")
    p.add_argument("--extracted", type=Path, default=ML / "data/raw/ahmed2025/extracted")
    args = p.parse_args(argv)

    sim = args.sim_dir.resolve()
    if sim.is_relative_to(BACKEND.resolve()):
        p.error("--sim-dir must be outside the repo")
    if (sim / "input").is_dir():  # drop links to the real dataset FIRST (never recurse into them)
        for child in (sim / "input").iterdir():
            bootstrap.rmtree(child)
    bootstrap.rmtree(sim)
    inputs, work, tmp = sim / "input", sim / "working" / "outputs", sim / "tmp"
    inputs.mkdir(parents=True)
    os.environ.update({"DENTISAI_INPUTS": str(inputs), "DENTISAI_WORK": str(work), "DENTISAI_TMP": str(tmp)})
    for k in [k for k in os.environ if k.startswith("KAGGLE_")]:
        del os.environ[k]  # compute_env() -> "local": missing GPU is a warning, licence gate env = local
    if args.code == "git":
        os.environ["DENTISAI_GIT_URL"] = make_git_source(sim / "gitsrc")
        install_fake_secret(FAKE_TOKEN)
    else:
        make_code_dataset(inputs)
    attach_data(args.data, inputs, args.zip, args.extracted)

    cells = load_cells(HERE / NOTEBOOKS[args.notebook])
    ns: dict[str, Any] = {"__name__": "__main__"}
    log = io.StringIO()
    results: list[tuple[int, str, str]] = []
    deep = args.level == "deep"
    install_cell, check_cell, config_cell, train_cell = 3, 4, 5, 6
    stubs_after_install: dict[str, Any] = {}
    with serve_file(args.zip) as url:
        os.environ["DENTISAI_ZENODO_URL"] = url
        for num, src in cells:
            if num == 0:
                src = patch_settings(src, smoke=not args.full)
            if num == 1 and args.min_free_gb is not None:
                src, n = re.subn(r"min_free_gb=[^,]+,", f"min_free_gb={args.min_free_gb},", src, count=1)
                results.append((1, "PATCHED" if n else "FAILED", f"preflight min_free_gb -> {args.min_free_gb}"))
            if not deep and num in (install_cell, check_cell):
                results.append((num, "SKIPPED", "needs Linux venvs / GPU toolchain (run --level deep in a container)"))
                if num == install_cell:
                    stubs_after_install = {"EXPORT_PY": sys.executable, "UV": ["uv"], "PRETRAINED_PTH": None, "GPUS": 0,
                                           "YOLOX_REPO": tmp / "YOLOX(not-cloned)", "MM_PY": "python3.10(not-installed)"}
                    ns.update(stubs_after_install)
                continue
            if not deep and num > config_cell and not src.lstrip().startswith(f"# {cells[-1][0]}."):
                results.append((num, "SKIPPED", "training/export (run --level deep)"))
                continue
            if deep and num == install_cell and args.cpu_locks:
                for name in ("MM_LOCK", "EXPORT_LOCK", "EXPORT_TF_LOCK"):
                    lock = getattr(ns["nbkit"], name)
                    setattr(ns["nbkit"], name, cpu_lock(lock, tmp / f"cpu_{lock.name}"))
            if deep and num == install_cell and args.no_tf:
                ns["nbkit"].EXPORT_TF_LOCK = ns["nbkit"].EXPORT_LOCK
                results.append((num, "PATCHED", "--no-tf: export venv without TensorFlow (tf_converter backend untestable)"))
            if deep and num == check_cell and args.no_tf:
                src = src.replace(", tensorflow as tf; print('onnx2tf ok, tf', tf.__version__)", "; print('onnx2tf ok (no TF)')")
            if deep and args.notebook == "yolox" and num == train_cell:
                ns["__sim_fake_train__"] = fake_yolox_train(ns)
                src = re.sub(r"^rc = nbkit\.sh\(\[sys\.executable, \"train\.py\".*$", "rc = __sim_fake_train__()", src, count=1, flags=re.M)
            ok, detail = run_cell(num, src, ns, log)
            results.append((num, "OK" if ok else "FAILED", detail))
            if num == 1 and args.code == "git":
                leaks = check_token_not_leaked(tmp, log.getvalue(), FAKE_TOKEN)
                results.append((num, "OK" if not leaks else "FAILED", "token leak check: " + ("clean" if not leaks else "; ".join(leaks))))
            if not ok:
                break
            if num == config_cell:
                probs = write_and_check_layouts(ns, tmp / "layout_check")
                results.append((num, "OK" if not probs else "FAILED", "trainer layouts: " + ("ok" if not probs else "; ".join(probs[:8]))))
    print("\n" + "=" * 30 + " SUMMARY " + "=" * 30)
    for num, status, detail in results:
        print(f"cell {num:>2}  {status:<8} {detail.strip().splitlines()[-1] if detail.strip() else ''}")
    failed = [r for r in results if r[1] == "FAILED"]
    for num, _, detail in failed:
        print(f"\n--- cell {num} failure ---\n{detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
