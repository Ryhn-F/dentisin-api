"""Notebook bootstrap: preflight checks + getting the pipeline code. STANDARD LIBRARY ONLY.

``make_ipynb.py`` INLINES this whole file into cell 1 of every Kaggle/Colab notebook (the pipeline
code -- including ``nbkit.py`` -- is not on the machine yet when cell 1 runs), so it must stay
self-contained. Messages for the product owner are in plain Indonesian; failures raise
:class:`SystemExit` so Jupyter shows the message without a traceback.

Code is found in this order (first hit wins):

1. an attached input that contains ``ml/detection/train.py`` (e.g. the private Kaggle dataset made
   by ``artifacts.py pack-code``) -- copied to a writable scratch dir;
2. ``git clone`` of the private repo :data:`REPO_URL` at ``CODE_REF`` with a GitHub token from the
   Kaggle Secret / Colab secret / env var ``GITHUB_TOKEN``. The token is passed to git ONLY through
   ``GIT_CONFIG_*`` environment variables (never argv, never ``.git/config``, never printed) and is
   scrubbed from any git output that is shown;
3. a clear explanation of what to add.
"""

from __future__ import annotations

import base64
import contextlib
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

BOOTSTRAP_VERSION = 1
#: nbkit.NBKIT_API must be >= this, otherwise the fetched code is older than the notebook.
REQUIRED_NBKIT_API = 4
REQUIRED_PY = (3, 11)
REPO_URL = "https://github.com/Ryhn-F/dentisin-api.git"
REPO_SLUG = "Ryhn-F/dentisin-api"
SECRET_NAME = "GITHUB_TOKEN"
CODE_MARKER = "ml/detection/train.py"
#: (host, port) pairs the notebooks need; checked by :func:`preflight`.
HOSTS_COMMON = (("github.com", 443), ("pypi.org", 443), ("files.pythonhosted.org", 443), ("zenodo.org", 443))
HOSTS_MEDIAPIPE = (("storage.googleapis.com", 443),)  # Model Maker downloads its MobileNetV2 checkpoint from here

RunFn = Callable[..., "subprocess.CompletedProcess[str]"]


def compute_env(environ: Mapping[str, str] | None = None, modules: Sequence[str] | None = None) -> str:
    env = os.environ if environ is None else environ
    mods = list(sys.modules) if modules is None else modules
    if any(k.startswith("KAGGLE_") for k in env):
        return "kaggle"
    if "google.colab" in mods or "COLAB_RELEASE_TAG" in env or "COLAB_GPU" in env:
        return "colab"
    return "local"


def default_dirs(env: str, base: Path | None = None, environ: Mapping[str, str] | None = None) -> dict[str, Path]:
    """``work`` = persisted outputs (Kaggle: /kaggle/working/outputs, saved with a committed version -- NO images
    there); ``tmp`` = scratch (code, venvs, photos, resized copies), never exported; ``inputs`` = read-only attached
    data. ``DENTISAI_WORK`` / ``DENTISAI_TMP`` / ``DENTISAI_INPUTS`` override (local simulation)."""
    if env == "kaggle":
        dirs = {"work": Path("/kaggle/working/outputs"), "tmp": Path("/tmp/dentisai"), "inputs": Path("/kaggle/input")}
    elif env == "colab":
        dirs = {"work": Path("/content/drive/MyDrive/dentisai_runs"), "tmp": Path("/content/tmp"),
                "inputs": Path("/content/drive/MyDrive/dentisai_inputs")}
    else:
        root = base or Path.cwd() / "nb_local"
        dirs = {"work": root / "working", "tmp": root / "tmp", "inputs": root / "input"}
    e = os.environ if environ is None else environ
    for key in dirs:
        if e.get(f"DENTISAI_{key.upper()}"):
            dirs[key] = Path(e[f"DENTISAI_{key.upper()}"])
    return dirs


def rmtree(path: Path) -> None:
    """shutil.rmtree that also removes read-only files (git objects on Windows); never follows links."""
    def onexc(func: Callable[..., Any], p: str, _exc: BaseException) -> None:
        os.chmod(p, 0o700)
        func(p)

    if path.is_symlink():
        path.unlink()
    elif hasattr(os.path, "isjunction") and os.path.isjunction(path):
        os.rmdir(path)  # removes the junction only, not its target
    elif path.exists():
        isjunction = getattr(os.path, "isjunction", lambda p: False)
        for d, dirs, files in os.walk(path):  # make everything writable first; never descend into links/junctions
            dirs[:] = [n for n in dirs if not (os.path.islink(os.path.join(d, n)) or isjunction(os.path.join(d, n)))]
            for name in dirs + files:
                p = os.path.join(d, name)
                if not os.path.islink(p):
                    with contextlib.suppress(OSError):
                        os.chmod(p, 0o700)
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=onexc)
        else:  # pragma: no cover - Python 3.11
            shutil.rmtree(path, onerror=lambda f, p, e: onexc(f, p, e[1]))


def _say(lines: Sequence[str]) -> str:
    bar = "=" * 78
    return "\n".join([bar, *lines, bar])


def fail(lines: Sequence[str]) -> SystemExit:
    """A plain-language stop (no traceback in Jupyter). Use as ``raise fail([...])``."""
    msg = _say(lines)
    print(msg, flush=True)
    return SystemExit(msg)


# ------------------------------------------------------------------------------------- preflight


def python_problem(version: Sequence[int] = tuple(sys.version_info[:3])) -> str | None:
    if tuple(version[:2]) < REQUIRED_PY:
        have = ".".join(str(v) for v in version[:3])
        return (f"Python kernel {have} terlalu lama (butuh >= {REQUIRED_PY[0]}.{REQUIRED_PY[1]}). Di Kaggle: panel kanan -> "
                "Settings -> Environment -> pilih 'Always use latest environment', lalu Run All lagi.")
    return None


def free_gb(path: Path) -> float:
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free / 2**30


def ram_gb(meminfo: Path = Path("/proc/meminfo")) -> float | None:
    try:
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) / 2**20
    except (OSError, ValueError, IndexError):
        return None
    return None


def gpu_names(run: RunFn = subprocess.run) -> list[str]:
    try:
        res = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                  capture_output=True, text=True, check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    return [ln.strip() for ln in (res.stdout or "").splitlines() if ln.strip()] if res.returncode == 0 else []


def can_reach(host: str, port: int = 443, timeout: float = 6.0, connect: Callable[..., Any] = socket.create_connection) -> bool:
    try:
        conn = connect((host, port), timeout=timeout)
    except OSError:
        return False
    with contextlib.suppress(OSError):
        conn.close()
    return True


def preflight(*, need_gpu: bool, hosts: Sequence[tuple[str, int]], disk_paths: Mapping[str, Path], min_free_gb: float,
              env: str, run: RunFn = subprocess.run, reach: Callable[[str, int], bool] = can_reach,
              version: Sequence[int] = tuple(sys.version_info[:3])) -> dict[str, Any]:
    """Print an environment report and stop with Indonesian instructions if something blocks the run.

    On a ``local`` machine (simulation) a missing GPU is only a warning."""
    gpus = gpu_names(run)
    reachable = {f"{h}:{p}": reach(h, p) for h, p in hosts}
    disks = {name: round(free_gb(p), 1) for name, p in disk_paths.items()}
    report: dict[str, Any] = {"python": ".".join(str(v) for v in version[:3]), "executable": sys.executable, "env": env,
                              "gpu": gpus or "TIDAK ADA", "ram_gb": None if (r := ram_gb()) is None else round(r, 1),
                              "free_disk_gb": disks, "internet": reachable}
    for k, v in report.items():
        print(f"{k:>13}: {v}")
    problems: list[str] = []
    if (pp := python_problem(version)) is not None:
        problems.append(pp)
    if not any(reachable.values()) and reachable:
        problems.append("Internet MATI. Di Kaggle: panel kanan -> Settings -> Internet -> On (perlu akun dengan nomor HP "
                        "terverifikasi: kaggle.com -> Settings -> Phone verification). Lalu Run All lagi.")
    elif not all(reachable.values()):
        down = ", ".join(k for k, ok in reachable.items() if not ok)
        problems.append(f"Tidak bisa menghubungi: {down}. Internet menyala tetapi situs itu tidak terjangkau -- coba lagi "
                        "beberapa menit lagi; bila tetap gagal, laporkan pesan ini.")
    if need_gpu and not gpus:
        msg = ("GPU MATI. Di Kaggle: panel kanan -> Settings -> Accelerator -> 'GPU T4 x2' (jangan P100), lalu Run All lagi. "
               "Bila pilihan GPU tidak ada: verifikasi nomor HP dan cek sisa kuota GPU mingguan.")
        if env == "local":
            print("PERINGATAN (lokal):", msg)
        else:
            problems.append(msg)
    if gpus and any("P100" in g for g in gpus):
        problems.append("GPU P100 tidak didukung PyTorch/TensorFlow terbaru. Ganti ke 'GPU T4 x2' (Settings -> Accelerator).")
    low = [f"{n} ({gb} GB)" for n, gb in disks.items() if gb < min_free_gb]
    if low:
        problems.append(f"Ruang disk kurang (butuh >= {min_free_gb} GB): {', '.join(low)}. Mulai sesi baru (Run -> Restart) "
                        "atau hapus berkas lama di /kaggle/working.")
    if problems:
        raise fail(["PERSIAPAN GAGAL -- perbaiki lalu jalankan ulang dari atas:", *[f"- {p}" for p in problems]])
    print("preflight: OK")
    return report


# ------------------------------------------------------------------------------------------ code


def get_secret(name: str = SECRET_NAME, environ: Mapping[str, str] | None = None) -> str | None:
    """Kaggle Secret -> Colab secret -> environment variable. Never logs the value."""
    try:
        from kaggle_secrets import UserSecretsClient

        value = UserSecretsClient().get_secret(name)
        if value:
            return str(value).strip()
    except Exception:  # module absent / secret not attached to this notebook: try the next source
        pass
    try:
        from google.colab import userdata

        value = userdata.get(name)
        if value:
            return str(value).strip()
    except Exception:
        pass
    env = os.environ if environ is None else environ
    return (env.get(name) or "").strip() or None


def find_code(roots: Sequence[Path], marker: str = CODE_MARKER, max_depth: int = 4) -> Path | None:
    """Directory R such that ``R / marker`` exists (R = the folder that contains ``ml/``)."""
    for root in roots:
        if not root.is_dir():
            continue
        for depth in range(max_depth):
            for hit in sorted(root.glob("/".join(["*"] * depth + [marker]))):
                return hit.parents[len(Path(marker).parts) - 1]
    return None


def scrub(text: str, secret: str | None) -> str:
    if not secret:
        return text
    for s in (secret, base64.b64encode(f"x-access-token:{secret}".encode()).decode()):
        text = text.replace(s, "***")
    return text


def git_auth_env(token: str | None, host: str = "github.com") -> dict[str, str]:
    """Environment for git: no prompts; the token travels only in GIT_CONFIG_* (git >= 2.31), like actions/checkout."""
    # core.autocrlf=false: byte-identical checkout everywhere (sha256-pinned split files must not gain CRLF on Windows)
    env = {"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.autocrlf", "GIT_CONFIG_VALUE_0": "false"}
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env.update({"GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_1": f"http.https://{host}/.extraheader",
                    "GIT_CONFIG_VALUE_1": f"AUTHORIZATION: basic {basic}"})
    return env


def classify_git_error(output: str) -> str:
    low = output.lower()
    if any(s in low for s in ("authentication failed", "could not read username", "403", "401", "invalid username or password",
                              "terminal prompts disabled")):
        return "auth"
    if "repository not found" in low or "not found" in low:
        return "not_found"
    if any(s in low for s in ("couldn't find remote ref", "unknown revision", "not our ref", "no such remote ref")):
        return "bad_ref"
    if any(s in low for s in ("could not resolve host", "failed to connect", "timed out", "network is unreachable")):
        return "network"
    return "other"


def git_clone(url: str, ref: str, dest: Path, token: str | None, *, run: RunFn = subprocess.run) -> str:
    """Shallow fetch of ``ref`` (branch, tag or full commit SHA) into a NEW ``dest``; returns the commit SHA.
    Raises RuntimeError(kind, scrubbed_output)."""
    rmtree(dest)
    dest.mkdir(parents=True)
    env = {**os.environ, **git_auth_env(token)}
    steps = [["git", "init", "-q", str(dest)],
             ["git", "-C", str(dest), "remote", "add", "origin", url],
             ["git", "-C", str(dest), "fetch", "-q", "--depth", "1", "origin", ref],
             ["git", "-C", str(dest), "checkout", "-q", "--detach", "FETCH_HEAD"],
             ["git", "-C", str(dest), "rev-parse", "HEAD"]]
    out = ""
    for cmd in steps:
        res = run(cmd, capture_output=True, text=True, env=env, check=False, timeout=600)
        out = scrub((res.stdout or "") + (res.stderr or ""), token)
        if res.returncode != 0:
            rmtree(dest)
            raise RuntimeError(classify_git_error(out), out.strip()[-2000:])
    return out.strip().splitlines()[-1] if out.strip() else ""


def token_help() -> list[str]:
    return [
        f"Cara membuat token (sekali saja, ±3 menit) -- akun GitHub yang punya akses ke repo {REPO_SLUG}:",
        "  1. github.com -> foto profil -> Settings -> Developer settings -> Personal access tokens -> Fine-grained tokens",
        "     -> Generate new token.",
        "  2. Token name: kaggle-dentisai; Expiration: 30-90 hari; Resource owner: pemilik repo; Repository access:",
        f"     'Only select repositories' -> {REPO_SLUG}; Permissions -> Repository permissions -> Contents: Read-only.",
        "     (Metadata: Read-only terisi otomatis.) Generate token -> salin (diawali 'github_pat_').",
        f"  3. Kaggle (notebook ini): menu Add-ons -> Secrets -> Add Secret: Label = {SECRET_NAME}, Value = token tadi -> Save,",
        "     lalu pastikan sakelar secret itu AKTIF untuk notebook ini. Jalankan ulang sel ini.",
        "  Jangan menempel token di sel kode, jangan membagikan notebook dengan secret di dalamnya.",
    ]


def acquire_code(*, roots: Sequence[Path], tmp: Path, ref: str, url: str = REPO_URL,
                 secret: Callable[[], str | None] = get_secret, run: RunFn = subprocess.run) -> tuple[Path, dict[str, Any]]:
    """Returns (writable folder containing ``ml/``, info). See the module docstring for the order."""
    url = os.environ.get("DENTISAI_GIT_URL", url)  # mirror / local simulation override
    attached = find_code(roots)
    src = tmp / "src"
    if attached is not None:
        rmtree(src)
        shutil.copytree(attached / "ml", src / "ml", ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".venv"))
        print(f"kode: dataset terlampir {attached} (disalin ke {src})")
        return src, {"source": "attached_dataset", "path": str(attached)}
    token = secret()
    if not token:
        raise fail(["KODE TIDAK DITEMUKAN. Notebook butuh kode pipeline DentisAI. Pilih SATU:",
                    f"A) (disarankan) Tambahkan Kaggle Secret bernama {SECRET_NAME} berisi token GitHub read-only.", *token_help(),
                    "B) Atau lampirkan dataset kode privat 'dentisai-ml-code' (panel kanan -> Add Input), dibuat dengan",
                    "   `uv run python artifacts.py pack-code --with-processed --out dentisai-ml-code.zip` (RUNBOOK §2)."])
    print(f"kode: git clone {url} @ {ref} (token dari secret {SECRET_NAME}; tidak dicetak)")
    for attempt in range(3):
        try:
            sha = git_clone(url, ref, src, token, run=run)
            break
        except RuntimeError as exc:
            kind, detail = (*exc.args, "", "")[:2]
            if kind == "network" and attempt < 2:
                time.sleep(10 * (attempt + 1))
                continue
            why = {"auth": f"Token di secret {SECRET_NAME} ditolak GitHub (salah, kedaluwarsa, atau tidak diberi akses repo {REPO_SLUG}).",
                   "not_found": f"Repo {REPO_SLUG} tidak terlihat dengan token ini (token fine-grained harus memilih repo itu, Contents: Read).",
                   "bad_ref": f"CODE_REF = {ref!r} tidak ada di repo (cek nama cabang/tag/commit di sel 0).",
                   "network": "Jaringan ke github.com gagal berulang kali (Internet harus On)."}.get(str(kind), "git clone gagal.")
            raise fail(["GAGAL MENGAMBIL KODE DARI GITHUB.", why, *(token_help() if kind in ("auth", "not_found") else []),
                        "Keluaran git (token disensor):", str(detail)[-1500:]]) from None
    if not (src / CODE_MARKER).is_file():
        raise fail([f"Repo terambil (commit {sha[:12]}) tetapi {CODE_MARKER} tidak ada. CODE_REF salah? Laporkan pesan ini."])
    print(f"kode: commit {sha}")
    return src, {"source": "git", "url": url, "ref": ref, "commit": sha}


def check_nbkit_api(api: int, required: int = REQUIRED_NBKIT_API) -> None:
    if api < required:
        raise fail([f"Kode yang terambil lebih lama dari notebook ini (nbkit API {api} < {required}).",
                    "Pastikan perubahan terbaru sudah di-push ke GitHub (cabang di CODE_REF), atau buat ulang dataset kode",
                    "dengan `artifacts.py pack-code`, lalu Run All lagi."])
