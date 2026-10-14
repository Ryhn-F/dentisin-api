"""kaggle/bootstrap.py (preflight, code acquisition) + kaggle/nbkit.py data/venv helpers + kaggle/local_simulate.py helpers.

Network and subprocesses are FAKED (in-memory Range server, fake runners); one test runs a real local
``git clone`` (file:// URL) to prove the token-free/no-leak clone path. All images are synthetic.
"""

from __future__ import annotations

import hashlib
import http.client
import io
import json
import os
import re
import subprocess
import sys
import types
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import bootstrap as bs
import local_simulate as sim
import make_ipynb
import nbkit

DET = Path(__file__).resolve().parents[1]
KAGGLE = DET / "kaggle"
U = "http://fake.invalid/Dataset.zip"
NOSLEEP: Callable[[float], None] = lambda s: None  # noqa: E731


# ------------------------------------------------------------------------------- fake HTTP server


class FakeResp(io.BytesIO):
    def __init__(self, data: bytes, status: int) -> None:
        super().__init__(data)
        self.status = status

    def getcode(self) -> int:
        return self.status


class FakeOpener:
    """Serves ``blob`` with Range support; ``fail`` = exceptions raised by the first calls."""

    def __init__(self, blob: bytes, *, fail: list[BaseException] | None = None, ignore_range: bool = False, short: int = 0) -> None:
        self.blob, self.fail, self.ignore_range, self.short, self.calls = blob, list(fail or []), ignore_range, short, 0

    def __call__(self, req: urllib.request.Request, timeout: float = 0) -> FakeResp:
        self.calls += 1
        if self.fail:
            raise self.fail.pop(0)
        rng = req.get_header("Range")
        if rng is None or self.ignore_range:
            return FakeResp(self.blob, 200)
        a, b = (int(x) for x in rng.split("=")[1].split("-"))
        data = self.blob[a:b + 1]
        if self.short:
            self.short -= 1
            data = data[:-1]
        return FakeResp(data, 206)


def make_zip(entries: dict[str, bytes], *, stored: tuple[str, ...] = ()) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Dataset/", b"")
        for name, data in entries.items():
            zf.writestr(name, data, compress_type=zipfile.ZIP_STORED if name in stored else zipfile.ZIP_DEFLATED)
    return buf.getvalue()


def jpeg_bytes(w: int = 20, h: int = 10) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), (120, 90, 60)).save(buf, "JPEG")
    return buf.getvalue()


IMG = "Dataset/Images/sub/View"
LAB = "Dataset/Annotations/Darknet_YOLO/sub/View"


def ahmed_like(n: int = 12) -> tuple[dict[str, bytes], dict[str, str]]:
    """Tiny Ahmed-shaped tree: n images, every other one with a YOLO label; split assignment for each."""
    entries: dict[str, bytes] = {}
    split_of: dict[str, str] = {}
    for i in range(n):
        img = f"{IMG}/img{i:02d}.jpg"
        entries[img] = jpeg_bytes()
        if i % 2 == 0:
            entries[f"{LAB}/img{i:02d}.txt"] = b"0 0.5 0.5 0.2 0.2\n" if i % 4 == 0 else b"1 0.3 0.3 0.1 0.1\n"
        split_of[img] = ("train", "val", "test")[i % 3]
    return entries, split_of


# -------------------------------------------------------------------------------------- bootstrap


def test_env_dirs_and_simple_probes(tmp_path: Path) -> None:
    assert bs.compute_env({"KAGGLE_KERNEL_RUN_TYPE": "x"}, []) == "kaggle"
    assert bs.compute_env({"COLAB_GPU": "1"}, []) == "colab"
    assert bs.default_dirs("kaggle", environ={})["work"] == Path("/kaggle/working/outputs")
    assert bs.default_dirs("local", tmp_path, environ={"DENTISAI_TMP": str(tmp_path / "t")})["tmp"] == tmp_path / "t"
    assert bs.default_dirs("colab", environ={})["inputs"].as_posix().endswith("dentisai_inputs")
    assert bs.python_problem((3, 10, 12)) is not None and bs.python_problem((3, 12, 0)) is None
    assert bs.free_gb(tmp_path / "does" / "not" / "exist") > 0
    mem = tmp_path / "meminfo"
    mem.write_text("MemTotal:       32768000 kB\n", encoding="utf-8")
    assert round(bs.ram_gb(mem) or 0) == 31 and bs.ram_gb(tmp_path / "nope") is None
    mem.write_text("Other: 1\n", encoding="utf-8")
    assert bs.ram_gb(mem) is None


def fake_run(stdout: str = "", rc: int = 0, exc: BaseException | None = None) -> Callable[..., Any]:
    def run(*a: Any, **k: Any) -> Any:
        if exc is not None:
            raise exc
        return SimpleNamespace(returncode=rc, stdout=stdout, stderr="")

    return run


def test_gpu_and_reach() -> None:
    assert bs.gpu_names(fake_run("Tesla T4, 15360 MiB\nTesla T4, 15360 MiB\n")) == ["Tesla T4, 15360 MiB"] * 2
    assert bs.gpu_names(fake_run(rc=9)) == [] and bs.gpu_names(fake_run(exc=FileNotFoundError())) == []

    class Conn:
        def close(self) -> None:
            raise OSError("already closed")

    assert bs.can_reach("h", connect=lambda *a, **k: Conn()) is True

    def refuse(*a: Any, **k: Any) -> Any:
        raise OSError("down")

    assert bs.can_reach("h", connect=refuse) is False


def test_preflight(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    disks = {"tmp": tmp_path}
    t4 = fake_run("Tesla T4, 15360 MiB\n")
    rep = bs.preflight(need_gpu=True, hosts=[("a", 1)], disk_paths=disks, min_free_gb=0.001, env="kaggle", run=t4,
                       reach=lambda h, p: True, version=(3, 12, 1))
    assert rep["gpu"] == ["Tesla T4, 15360 MiB"] and "preflight: OK" in capsys.readouterr().out
    cases = [
        ({"run": fake_run(), "env": "kaggle"}, "GPU MATI"),
        ({"reach": lambda h, p: False}, "Internet MATI"),
        ({"reach": lambda h, p: h == "a"}, "Tidak bisa menghubungi: b:1"),
        ({"run": fake_run("Tesla P100-PCIE-16GB, 16280 MiB\n")}, "P100"),
        ({"min_free_gb": 1e9}, "Ruang disk kurang"),
        ({"version": (3, 10, 4)}, "terlalu lama"),
    ]
    for over, text in cases:
        kw: dict[str, Any] = {"need_gpu": True, "hosts": [("a", 1), ("b", 1)], "disk_paths": disks, "min_free_gb": 0.001, "env": "kaggle",
                              "run": t4, "reach": lambda h, p: True, "version": (3, 12, 1)}
        kw.update(dict(over))  # type: ignore[call-overload]
        with pytest.raises(SystemExit, match=text):
            bs.preflight(**kw)
    bs.preflight(need_gpu=True, hosts=[], disk_paths=disks, min_free_gb=0.001, env="local", run=fake_run(), version=(3, 11, 0))
    assert "PERINGATAN (lokal)" in capsys.readouterr().out


def test_get_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "kaggle_secrets", raising=False)
    assert bs.get_secret(environ={"GITHUB_TOKEN": " tok "}) == "tok"
    assert bs.get_secret(environ={}) is None
    mod = types.ModuleType("kaggle_secrets")
    mod.UserSecretsClient = lambda: SimpleNamespace(get_secret=lambda name: "kaggle-tok")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "kaggle_secrets", mod)
    assert bs.get_secret(environ={}) == "kaggle-tok"
    mod.UserSecretsClient = lambda: SimpleNamespace(get_secret=lambda name: (_ for _ in ()).throw(KeyError(name)))  # type: ignore[attr-defined]
    colab = types.ModuleType("google.colab")
    colab.userdata = SimpleNamespace(get=lambda name: "colab-tok")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google.colab", colab)
    assert bs.get_secret(environ={}) == "colab-tok"


def test_git_helpers() -> None:
    env = bs.git_auth_env("SECRET")
    assert env["GIT_TERMINAL_PROMPT"] == "0" and env["GIT_CONFIG_COUNT"] == "2" and "SECRET" not in json.dumps(env)
    assert env["GIT_CONFIG_KEY_0"] == "core.autocrlf" and env["GIT_CONFIG_VALUE_1"].startswith("AUTHORIZATION: basic ")
    assert bs.git_auth_env(None)["GIT_CONFIG_COUNT"] == "1"
    basic = env["GIT_CONFIG_VALUE_1"].split()[-1]
    assert bs.scrub(f"x SECRET y {basic}", "SECRET") == "x *** y ***" and bs.scrub("x", None) == "x"
    assert bs.classify_git_error("fatal: Authentication failed for") == "auth"
    assert bs.classify_git_error("remote: Repository not found.") == "not_found"
    assert bs.classify_git_error("fatal: couldn't find remote ref nope") == "bad_ref"
    assert bs.classify_git_error("Could not resolve host: github.com") == "network"
    assert bs.classify_git_error("weird") == "other"


@pytest.fixture
def git_repo(tmp_path: Path) -> str:
    repo = tmp_path / "origin"
    (repo / "ml" / "detection").mkdir(parents=True)
    (repo / "ml" / "detection" / "train.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "ml" / "data").mkdir()
    (repo / "ml" / "data" / "crlf.json").write_bytes(b'{"a": 1}\n')
    g = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run([*g, "add", "-A"], check=True)
    subprocess.run([*g, "commit", "-q", "-m", "init"], check=True)
    return repo.resolve().as_uri()


def test_git_clone_real_local_repo_without_token_leak(tmp_path: Path, git_repo: str) -> None:
    dest = tmp_path / "clone"
    sha = bs.git_clone(git_repo, "main", dest, "SECRET-TOKEN-123")
    assert len(sha) == 40 and (dest / "ml" / "detection" / "train.py").is_file()
    assert (dest / "ml" / "data" / "crlf.json").read_bytes() == b'{"a": 1}\n'  # byte-identical (autocrlf off)
    for p in (dest / ".git").rglob("*"):
        assert not p.is_file() or b"SECRET-TOKEN-123" not in p.read_bytes()
    with pytest.raises(RuntimeError) as ei:
        bs.git_clone(git_repo, "no-such-branch", tmp_path / "c2", "SECRET-TOKEN-123")
    assert ei.value.args[0] == "bad_ref" and "SECRET" not in str(ei.value.args[1]) and not (tmp_path / "c2").exists()


def test_acquire_code_paths(tmp_path: Path, git_repo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    inp = tmp_path / "input" / "dentisai-ml-code"
    (inp / "ml" / "detection").mkdir(parents=True)
    (inp / "ml" / "detection" / "train.py").write_text("", encoding="utf-8")
    src, info = bs.acquire_code(roots=[tmp_path / "input"], tmp=tmp_path / "tmp", ref="main")
    assert info["source"] == "attached_dataset" and (src / "ml" / "detection" / "train.py").is_file()
    roots = [tmp_path / "empty"]
    with pytest.raises(SystemExit, match="GITHUB_TOKEN"):
        bs.acquire_code(roots=roots, tmp=tmp_path / "t1", ref="main", secret=lambda: None)
    monkeypatch.setenv("DENTISAI_GIT_URL", git_repo)
    src, info = bs.acquire_code(roots=roots, tmp=tmp_path / "t2", ref="main", secret=lambda: "tok")
    assert info["source"] == "git" and len(info["commit"]) == 40
    assert "tok" not in capsys.readouterr().out.replace("token", "")
    monkeypatch.setattr("bootstrap.time.sleep", NOSLEEP)
    calls = {"n": 0}

    def flaky(cmd: list[str], **kw: Any) -> Any:
        if cmd[3:4] == ["fetch"] and calls["n"] < 1:
            calls["n"] += 1
            return SimpleNamespace(returncode=128, stdout="", stderr="fatal: unable to access: Could not resolve host: github.com")
        return subprocess.run(cmd, **kw)

    src, info = bs.acquire_code(roots=roots, tmp=tmp_path / "t3", ref="main", secret=lambda: "tok", run=flaky)
    assert calls["n"] == 1 and info["source"] == "git"
    def auth_fail(cmd: list[str], **kw: Any) -> Any:
        return SimpleNamespace(returncode=128, stdout="", stderr="remote: Invalid username or password. tok")

    with pytest.raises(SystemExit, match="ditolak GitHub") as ei:
        bs.acquire_code(roots=roots, tmp=tmp_path / "t4", ref="main", secret=lambda: "tok", run=auth_fail)
    assert "Fine-grained" in str(ei.value) and " tok" not in str(ei.value).split("Keluaran git")[1]
    with pytest.raises(SystemExit, match="CODE_REF"):
        bs.acquire_code(roots=roots, tmp=tmp_path / "t5", ref="nope", secret=lambda: "tok")

    def no_marker(cmd: list[str], **kw: Any) -> Any:
        return SimpleNamespace(returncode=0, stdout="abc\n", stderr="")

    with pytest.raises(SystemExit, match="tidak ada"):
        bs.acquire_code(roots=roots, tmp=tmp_path / "t6", ref="main", secret=lambda: "tok", run=no_marker)


def test_api_check_find_code_and_rmtree(tmp_path: Path) -> None:
    bs.check_nbkit_api(nbkit.NBKIT_API)
    with pytest.raises(SystemExit, match="lebih lama"):
        bs.check_nbkit_api(1)
    assert bs.find_code([tmp_path / "missing"]) is None
    ro = tmp_path / "ro" / "f.txt"
    ro.parent.mkdir()
    ro.write_text("x", encoding="utf-8")
    os.chmod(ro, 0o400)
    bs.rmtree(tmp_path / "ro")
    assert not (tmp_path / "ro").exists()
    target = tmp_path / "target"
    target.mkdir()
    (target / "keep.txt").write_text("k", encoding="utf-8")
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not permitted here")
    bs.rmtree(link)
    assert (target / "keep.txt").is_file() and not link.exists()


# ---------------------------------------------------------------------------------- nbkit: HTTP


def test_fetch_range_and_pick_url() -> None:
    blob = bytes(range(256)) * 4
    assert nbkit.fetch_range(U, 10, 19, opener=FakeOpener(blob)) == blob[10:20]
    op = FakeOpener(blob, fail=[urllib.error.URLError("dns"), TimeoutError()])
    assert nbkit.fetch_range(U, 0, 3, opener=op, sleep=NOSLEEP) == blob[:4] and op.calls == 3
    assert nbkit.fetch_range(U, 0, 3, opener=FakeOpener(blob, short=1), sleep=NOSLEEP) == blob[:4]
    # Kaggle 2026-10-14: Zenodo cut a Range response short -> http.client.IncompleteRead (an HTTPException, not OSError)
    op = FakeOpener(blob, fail=[http.client.IncompleteRead(b"abc", 5), http.client.RemoteDisconnected("closed")])
    assert nbkit.fetch_range(U, 4, 7, opener=op, sleep=NOSLEEP) == blob[4:8] and op.calls == 3
    with pytest.raises(nbkit.RangeNotSupportedError):
        nbkit.fetch_range(U, 0, 3, opener=FakeOpener(blob, ignore_range=True))
    with pytest.raises(nbkit.DataError, match="after 1 attempts"):
        nbkit.fetch_range(U, 0, 3, opener=FakeOpener(blob, fail=[urllib.error.HTTPError("u", 404, "nf", {}, None)]), sleep=NOSLEEP)  # type: ignore[arg-type]
    with pytest.raises(nbkit.DataError, match="after 3 attempts"):
        nbkit.fetch_range(U, 0, 3, opener=FakeOpener(blob, fail=[ConnectionError()] * 5), retries=2, sleep=NOSLEEP)
    assert nbkit.pick_url([U, U + "2"], len(blob), opener=FakeOpener(blob)) == U
    with pytest.raises(nbkit.DataError, match="no download URL"):
        nbkit.pick_url([U], len(blob), opener=FakeOpener(blob, ignore_range=True), sleep=NOSLEEP)


def test_download_parallel_resume_and_md5(tmp_path: Path) -> None:
    blob = os.urandom(100_000)
    md5 = hashlib.md5(blob).hexdigest()
    dest = tmp_path / "d" / "Dataset.zip"
    logs: list[str] = []
    out = nbkit.download_parallel([U], dest, size=len(blob), md5=md5, chunk=7_000, workers=4, opener=FakeOpener(blob), log=logs.append)
    assert out.read_bytes() == blob and (tmp_path / "d" / "Dataset.zip.md5ok").is_file()
    op = FakeOpener(blob)
    nbkit.download_parallel([U], dest, size=len(blob), md5=md5, opener=op, log=logs.append)
    assert op.calls == 0 and "already downloaded" in logs[-1]
    dest.unlink()
    part = dest.with_name("Dataset.zip.part")
    part.write_bytes(blob[:7_000] + b"\0" * (len(blob) - 7_000))  # chunk 0 done in an earlier session
    dest.with_name("Dataset.zip.parts.json").write_text(json.dumps({"size": len(blob), "chunk": 7_000, "done": [0]}), encoding="utf-8")
    op = FakeOpener(blob)
    nbkit.download_parallel([U], dest, size=len(blob), md5=md5, chunk=7_000, workers=2, opener=op, log=logs.append)
    assert dest.read_bytes() == blob and op.calls == 1 + 14  # url probe + 14 remaining chunks
    dest.unlink()
    with pytest.raises(nbkit.DataError, match="MD5 mismatch"):
        nbkit.download_parallel([U], dest, size=len(blob), md5="0" * 32, chunk=50_000, opener=FakeOpener(blob), log=logs.append)
    assert not part.exists()


def test_remote_zip_and_archives(tmp_path: Path) -> None:
    long_extra = "Dataset/Images/sub/View/big.jpg"
    entries = {"Dataset/Images/sub/View/a.jpg": jpeg_bytes(), "Dataset/Annotations/Darknet_YOLO/sub/View/a.txt": b"0 .5 .5 .1 .1\n",
               long_extra: os.urandom(3000)}
    blob = make_zip(entries, stored=("Dataset/Annotations/Darknet_YOLO/sub/View/a.txt",))
    op = FakeOpener(blob)
    arc = nbkit.Archive.from_url(U, len(blob), opener=op, sleep=NOSLEEP)
    assert sorted(arc.names) == sorted(entries) and arc.kind == "remote"
    for n, data in entries.items():
        assert arc.read(n) == data
    n_new = arc.extract(list(entries), tmp_path / "x", log=lambda s: None)
    assert n_new == 3 and (tmp_path / "x" / long_extra).read_bytes() == entries[long_extra]
    assert arc.extract(list(entries), tmp_path / "x", log=lambda s: None) == 0  # already there
    zpath = tmp_path / "local.zip"
    zpath.write_bytes(blob)
    local = nbkit.Archive.from_zip(zpath)
    assert local.read("Dataset/Images/sub/View/a.jpg") == entries["Dataset/Images/sub/View/a.jpg"]
    local.extract(list(entries)[:1], tmp_path / "y", log=lambda s: None)
    d = nbkit.Archive.from_dir(tmp_path / "x")
    assert sorted(d.names) == sorted(entries) and d.read(long_extra) == entries[long_extra]
    rf = nbkit.RemoteFile(U, len(blob), opener=FakeOpener(blob), block=16)
    assert rf.seekable() and rf.readable() and rf.seek(-4, 2) == len(blob) - 4 and rf.read() == blob[-4:] and rf.read(5) == b""
    rf.seek(0)
    assert rf.read(2) == blob[:2] and rf.tell() == 2 and rf.seek(3, 1) == 5
    rf.close()
    bad = zipfile.ZipInfo("x")
    bad.header_offset, bad.compress_size = 0, 1
    with pytest.raises(nbkit.DataError, match="bad local header"):
        nbkit.read_remote_member(U, 64, bad, opener=FakeOpener(b"\0" * 64))
    info = zipfile.ZipFile(io.BytesIO(blob)).getinfo("Dataset/Images/sub/View/a.jpg")
    info.CRC ^= 1
    with pytest.raises(nbkit.DataError, match="CRC"):
        nbkit.read_remote_member(U, len(blob), info, opener=FakeOpener(blob))
    info.CRC ^= 1
    info.compress_type = 12
    with pytest.raises(nbkit.DataError, match="unsupported compression"):
        nbkit.read_remote_member(U, len(blob), info, opener=FakeOpener(blob))


def test_safe_member_path(tmp_path: Path) -> None:
    assert nbkit.safe_member_path(tmp_path, "Dataset/Images/a.jpg") == tmp_path / "Dataset" / "Images" / "a.jpg"
    for bad in ("../x", "/etc/passwd", "C:/x", "a/../../b", ""):
        with pytest.raises(nbkit.DataError, match="unsafe"):
            nbkit.safe_member_path(tmp_path, bad)


def test_labels_smoke_pick_and_subset(tmp_path: Path) -> None:
    entries, split_of = ahmed_like(30)
    idx = nbkit.label_index(entries)
    assert nbkit.label_for(f"{IMG}/img00.jpg", idx) == f"{LAB}/img00.txt" and nbkit.label_for(f"{IMG}/img01.jpg", idx) is None
    idx2 = {"img00": ["Dataset/Annotations/Darknet_YOLO/other/img00.txt"], "dup": ["a/dup.txt", "b/dup.txt"]}
    assert nbkit.label_for(f"{IMG}/img00.jpg", idx2) == idx2["img00"][0] and nbkit.label_for(f"{IMG}/dup.jpg", idx2) is None
    picked = nbkit.smoke_pick(split_of, lambda img: int(img[-6:-4]) % 2 == 0, n={"train": 4, "val": 2})
    assert {k: len(v) for k, v in picked.items()} == {"train": 4, "val": 2}
    assert sum(int(i[-6:-4]) % 2 == 0 for i in picked["train"]) == 3  # 75 % positives
    assert picked == nbkit.smoke_pick(split_of, lambda img: int(img[-6:-4]) % 2 == 0, n={"train": 4, "val": 2})  # deterministic
    assert len(nbkit.smoke_pick(split_of, lambda img: True, n={"train": 4})["train"]) == 4  # all positive: fills with positives
    with pytest.raises(nbkit.DataError, match="no images"):
        nbkit.smoke_pick(split_of, lambda img: True, n={"nope": 2})
    full = json.dumps({"images": split_of, "image_groups": {k: f"g{i}" for i, k in enumerate(split_of)},
                       "groups": {f"g{i}": s for i, s in enumerate(split_of.values())}}).encode()
    man = nbkit.subset_splits(full, picked["train"], tmp_path / "s")
    sub = json.loads((tmp_path / "s" / "splits.json").read_text(encoding="utf-8"))
    assert set(sub["images"]) == set(picked["train"]) and len(sub["groups"]) == 4 and man["smoke_subset_of"] == hashlib.sha256(full).hexdigest()
    with pytest.raises(nbkit.DataError, match="not in the committed"):
        nbkit.subset_splits(full, ["nope.jpg"], tmp_path / "s2")


def test_committed_splits(tmp_path: Path) -> None:
    d = tmp_path / "ml" / "data" / "splits" / "ahmed2025"
    assert nbkit.committed_splits(tmp_path / "ml") is None
    d.mkdir(parents=True)
    lf = b'{\n "images": {}\n}\n'
    (d / "splits.json").write_bytes(lf.replace(b"\n", b"\r\n"))
    (d / "split_manifest.json").write_text(json.dumps({"splits_sha256": hashlib.sha256(lf).hexdigest()}), encoding="utf-8")
    got = nbkit.committed_splits(tmp_path / "ml")
    assert got is not None and got[0] == lf
    (d / "splits.json").write_bytes(b"{}")
    with pytest.raises(nbkit.DataError, match="damaged"):
        nbkit.committed_splits(tmp_path / "ml")


# ------------------------------------------------------------------------- nbkit: data preparation


def write_tree(root: Path, entries: dict[str, bytes]) -> None:
    for n, data in entries.items():
        (root / n).parent.mkdir(parents=True, exist_ok=True)
        (root / n).write_bytes(data)


def fake_ml(tmp_path: Path, split_of: dict[str, str]) -> Path:
    """A minimal ml/ tree (class map + committed split) next to the real pipeline code."""
    ml = tmp_path / "ml"
    (ml / "data" / "class_maps").mkdir(parents=True)
    (ml / "data" / "class_maps" / "ahmed2025.yaml").write_text(
        "status: decided\nyolo_names: [D, d]\nmap:\n  D: caries_permanent\n  d: caries_primary\n", encoding="utf-8")
    sp = ml / "data" / "splits" / "ahmed2025"
    sp.mkdir(parents=True)
    raw = json.dumps({"images": split_of, "image_groups": {k: k for k in split_of}, "groups": dict(split_of)}).encode()
    (sp / "splits.json").write_bytes(raw)
    (sp / "split_manifest.json").write_text(json.dumps({"splits_sha256": hashlib.sha256(raw).hexdigest(), "group_quality": "heuristic_session",
                                                        "image_counts": {}}), encoding="utf-8")
    return ml


def inproc_runner(calls: list[list[str]]) -> Callable[..., int]:
    """Runs convert_annotations / make_splits / ahmed2025_groups in-process (instead of a subprocess)."""
    import convert_annotations

    def run(cmd: list[Any], cwd: Path | None = None, **kw: Any) -> int:
        args = [str(c) for c in cmd]
        calls.append(args)
        if args[1].endswith("convert_annotations.py"):
            return convert_annotations.main(args[2:])
        raise AssertionError(f"unexpected command {args[:2]}")

    return run


def test_prepare_ahmed_all_routes_and_build_processed(tmp_path: Path) -> None:
    entries, split_of = ahmed_like(30)
    blob = make_zip(entries)
    md5 = hashlib.md5(blob).hexdigest()
    logs: list[str] = []
    log = logs.append
    # (1) nothing attached, SMOKE_TEST -> remote zip by Range requests
    smoke_n = {"train": 4, "val": 2, "test": 2}
    old = nbkit.SMOKE_N.copy()
    nbkit.SMOKE_N.clear()
    nbkit.SMOKE_N.update(smoke_n)
    try:
        res = nbkit.prepare_ahmed(inputs=[tmp_path / "in"], tmp=tmp_path / "t1", smoke=True, split_of=split_of, urls=[U], size=len(blob),
                                  md5=md5, opener=FakeOpener(blob), sleep=NOSLEEP, log=log)
        assert res["source"] == "zenodo_range" and len(res["images"]) == 8
        assert all((res["root"] / i).is_file() for i in res["images"])
        ml = fake_ml(tmp_path, split_of)
        calls: list[list[str]] = []
        info = nbkit.build_processed(src_ml=ml, data_root=res["root"], processed=tmp_path / "p1", images=res["images"], run=inproc_runner(calls))
        assert info["n_images"] == 8 and info["group_quality"] == "smoke_subset" and info["split_source"] == "committed"
        assert set(info["boxes_per_class"]) <= {"caries_permanent", "caries_primary"}
        # (2) attached extracted folder, smoke -> copies the subset; full -> used in place
        write_tree(tmp_path / "in2" / "ahmed", entries)
        res2 = nbkit.prepare_ahmed(inputs=[tmp_path / "in2"], tmp=tmp_path / "t2", smoke=True, split_of=split_of, log=log)
        assert res2["source"] == "attached_dir" and res2["images"] == res["images"]
        res3 = nbkit.prepare_ahmed(inputs=[tmp_path / "in2"], tmp=tmp_path / "t3", smoke=False, split_of=split_of, log=log)
        assert res3 == {"root": tmp_path / "in2" / "ahmed", "source": "attached_dir", "images": None}
        info = nbkit.build_processed(src_ml=ml, data_root=res3["root"], processed=tmp_path / "p3", images=None, run=inproc_runner(calls))
        assert info["n_images"] == 30 and info["splits_sha256"] == nbkit.sha256_file(ml / "data/splits/ahmed2025/splits.json")
        # (3) attached zip (right size), full -> extracted to tmp
        (tmp_path / "in4" / "ds").mkdir(parents=True)
        (tmp_path / "in4" / "ds" / "Dataset.zip").write_bytes(blob)
        res4 = nbkit.prepare_ahmed(inputs=[tmp_path / "in4"], tmp=tmp_path / "t4", smoke=False, split_of=split_of, size=len(blob), log=log)
        assert res4["source"] == "attached_zip" and len(list((res4["root"] / "Dataset/Images").rglob("*.jpg"))) == 30
        # (4) nothing attached, full -> parallel download (MD5) + extraction; zip removed afterwards
        res5 = nbkit.prepare_ahmed(inputs=[tmp_path / "in"], tmp=tmp_path / "t5", smoke=False, split_of=split_of, urls=[U], size=len(blob),
                                   md5=md5, opener=FakeOpener(blob), sleep=NOSLEEP, log=log, free=lambda p: 1e6)
        assert res5["source"] == "zenodo_download" and not (tmp_path / "t5" / "download" / "Dataset.zip").exists()
        with pytest.raises(nbkit.DataError, match="GB free"):
            nbkit.prepare_ahmed(inputs=[tmp_path / "in"], tmp=tmp_path / "t6", smoke=False, split_of=split_of, urls=[U], size=len(blob),
                                md5=md5, opener=FakeOpener(blob), log=log, free=lambda p: 0.0)
        only_ghost_val = {**{k: v for k, v in split_of.items() if v != "val"}, f"{IMG}/ghost.jpg": "val"}
        with pytest.raises(nbkit.DataError, match="not in the dataset"):
            nbkit.prepare_ahmed(inputs=[tmp_path / "in2"], tmp=tmp_path / "t7", smoke=True, split_of=only_ghost_val, log=log)
    finally:
        nbkit.SMOKE_N.clear()
        nbkit.SMOKE_N.update(old)
    assert any("CC BY 4.0" in m for m in logs)
    # disagreement between data and split, and a class map with an undecided label
    (ml / "data/splits/ahmed2025/splits.json").write_bytes(b"")
    with pytest.raises(nbkit.DataError, match="damaged"):
        nbkit.build_processed(src_ml=ml, data_root=res3["root"], processed=tmp_path / "p4", images=None, run=inproc_runner(calls))
    fake_ml(tmp_path / "x", {f"{IMG}/img00.jpg": "train"})
    with pytest.raises(nbkit.DataError, match="disagree"):
        nbkit.build_processed(src_ml=tmp_path / "x" / "ml", data_root=res3["root"], processed=tmp_path / "p5", images=None, run=inproc_runner(calls))
    (tmp_path / "x/ml/data/class_maps/ahmed2025.yaml").write_text("status: decided\nmap:\n  D: null\n", encoding="utf-8")
    with pytest.raises(nbkit.DataError, match="fill ml/data/class_maps"):
        nbkit.build_processed(src_ml=tmp_path / "x" / "ml", data_root=res3["root"], processed=tmp_path / "p6", images=None, run=inproc_runner(calls))


def test_build_processed_regenerates_split_when_missing(tmp_path: Path) -> None:
    entries, split_of = ahmed_like(6)
    write_tree(tmp_path / "root", entries)
    ml = fake_ml(tmp_path, split_of)
    for f in (ml / "data/splits/ahmed2025").iterdir():
        f.unlink()
    calls: list[list[str]] = []
    base = inproc_runner(calls)

    def run(cmd: list[Any], cwd: Path | None = None, **kw: Any) -> int:
        args = [str(c) for c in cmd]
        if args[1].endswith("make_splits.py"):
            out = Path(args[args.index("--out-dir") + 1])
            out.mkdir(parents=True, exist_ok=True)
            raw = json.dumps({"images": split_of}).encode()
            (out / "splits.json").write_bytes(raw)
            (out / "split_manifest.json").write_text(json.dumps({"splits_sha256": hashlib.sha256(raw).hexdigest()}), encoding="utf-8")
            calls.append(args)
            return 0
        if args[1].endswith("ahmed2025_groups.py"):
            calls.append(args)
            return 0
        return base(cmd, cwd=cwd)

    info = nbkit.build_processed(src_ml=ml, data_root=tmp_path / "root", processed=tmp_path / "p", images=None, run=run)
    assert info["split_source"] == "regenerated" and any(c[1].endswith("ahmed2025_groups.py") for c in calls)
    assert "session_csv" in next(c for c in calls if c[1].endswith("make_splits.py"))
    with pytest.raises(nbkit.DataError, match="SMOKE_TEST needs"):
        nbkit.build_processed(src_ml=ml, data_root=tmp_path / "root", processed=tmp_path / "p2", images={f"{IMG}/img00.jpg"}, run=run)


def test_locate_find_named_and_mini_layout(tmp_path: Path) -> None:
    entries, split_of = ahmed_like(12)
    assert nbkit.locate_ahmed([tmp_path / "none"]) is None
    (tmp_path / "in" / "a").mkdir(parents=True)
    (tmp_path / "in" / "a" / "Dataset.zip").write_bytes(b"12345")
    assert nbkit.locate_ahmed([tmp_path / "in"], size=5) == ("zip", tmp_path / "in" / "a" / "Dataset.zip")
    assert nbkit.locate_ahmed([tmp_path / "in"], size=6) is None
    (tmp_path / "in" / "old_smoke" / "yolox_outputs").mkdir(parents=True)
    (tmp_path / "in" / "v1" / "outputs" / "yolox_outputs").mkdir(parents=True)
    found = nbkit.find_named([tmp_path / "in"], "yolox_outputs", want_dir=True, exclude=("_smoke",))
    assert found == [tmp_path / "in" / "v1" / "outputs" / "yolox_outputs"]
    write_tree(tmp_path / "root", entries)
    ml = fake_ml(tmp_path, split_of)
    info = nbkit.build_processed(src_ml=ml, data_root=tmp_path / "root", processed=tmp_path / "p", images=None, run=inproc_runner([]))
    assert info["n_images"] == 12
    P = nbkit.processed_paths(tmp_path / "p")
    counts = nbkit.mini_layout(P["canonical"], P["splits"], tmp_path / "root", tmp_path / "mini", layout="mediapipe", n={"train": 2, "val": 1})
    assert counts == {"train": 2, "val": 1}
    assert not sim.check_mediapipe_layout(tmp_path / "mini" / "data" / "train", min_annotated=1)
    with pytest.raises(nbkit.DataError, match="no nope images"):
        nbkit.mini_layout(P["canonical"], P["splits"], tmp_path / "root", tmp_path / "mini2", layout="yolox", n={"nope": 1})
    assert nbkit.canonical_images(P["canonical"])[0].startswith("Dataset/Images/")


# ------------------------------------------------------------------------- nbkit: envs & outputs


def test_missing_modules_and_uv() -> None:
    assert nbkit.missing_modules({"json": "j", "no_such_mod_xyz": "pkg==1"}) == ["pkg==1"]

    def boom(name: str) -> Any:
        raise ValueError(name)

    assert nbkit.missing_modules({"x": "x"}, find_spec=boom) == ["x"]
    assert nbkit.uv_command(which=lambda n: "/usr/bin/uv") == ["/usr/bin/uv"]
    seen: list[list[str]] = []
    def record(c: list[Any], **k: Any) -> int:
        seen.append([str(x) for x in c])
        return 0

    assert nbkit.uv_command(which=lambda n: None, run=record)[1:] == ["-m", "uv"]
    assert seen and seen[0][-1] == f"uv=={nbkit.UV_VERSION}"


def test_create_and_ensure_venv(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    state = {"uv_ok": False, "conda_ok": True}

    def run(cmd: list[Any], check: bool = True, **kw: Any) -> int:
        args = [str(c) for c in cmd]
        calls.append(args)
        if "venv" in args and "--python" in args:
            if state["uv_ok"]:
                nbkit.venv_python(Path(args[-1])).parent.mkdir(parents=True, exist_ok=True)
                nbkit.venv_python(Path(args[-1])).write_text("", encoding="utf-8")
            return 0 if state["uv_ok"] else 1
        if args[1:2] == ["create"]:
            path = Path(args[args.index("-p") + 1])
            nbkit.venv_python(path).parent.mkdir(parents=True, exist_ok=True)
            nbkit.venv_python(path).write_text("", encoding="utf-8")
            return 0 if state["conda_ok"] else 1
        return 0

    def version(py: Path) -> str:
        return "3.10" if py.exists() else ""

    py = nbkit.create_venv(tmp_path / "v1", "3.10", uv=["uv"], run=run, which=lambda n: "/opt/conda/bin/conda" if n == "conda" else None,
                           version_of=version)
    assert py == nbkit.venv_python(tmp_path / "v1") and any(c[1:2] == ["create"] for c in calls)
    assert nbkit.create_venv(tmp_path / "v1", "3.10", uv=["uv"], run=run, version_of=version) == py  # reused
    state["conda_ok"] = False
    with pytest.raises(RuntimeError, match="conda create"):
        nbkit.create_venv(tmp_path / "v2", "3.10", uv=["uv"], run=run, which=lambda n: "/c" if n == "conda" else None, version_of=lambda p: "")
    with pytest.raises(RuntimeError, match="conda \\(not installed\\)"):
        nbkit.create_venv(tmp_path / "v3", "3.10", uv=["uv"], run=run, which=lambda n: None, version_of=lambda p: "")
    state["uv_ok"] = True
    lock = tmp_path / "lock.txt"
    lock.write_text("x==1\n", encoding="utf-8")
    py = nbkit.ensure_venv(tmp_path / "v4", "3.10", lock, uv=["uv"], run=run, version_of=version)
    assert calls[-1][-2:] == ["-r", str(lock)] and py.exists()
    nbkit.ensure_venv(tmp_path / "v4", "3.10", ["a==1"], uv=["uv"], run=run, version_of=version)
    assert calls[-1][-1] == "a==1"
    real = nbkit.python_version_of(Path(sys.executable))
    assert real == f"{sys.version_info[0]}.{sys.version_info[1]}"


def test_tf_gpu_helpers(tmp_path: Path) -> None:
    venv = tmp_path / "venv"
    lib = venv / "lib" / "python3.10" / "site-packages" / "nvidia" / "cudnn" / "lib"
    lib.mkdir(parents=True)
    assert nbkit.nvidia_lib_dirs(venv) == [str(lib)]

    def run_with(counts: list[int]) -> Callable[..., Any]:
        def run(cmd: list[str], **kw: Any) -> Any:
            n = counts.pop(0)
            return SimpleNamespace(stdout=f"TF 2.15.1\nGPUS={n}\n" if n >= 0 else "", stderr="ImportError" if n < 0 else "")

        return run

    env: dict[str, str] = {}
    assert nbkit.ensure_tf_gpu(Path("py"), venv, need=True, run=run_with([2]), environ=env) == 2 and env == {}
    assert nbkit.ensure_tf_gpu(Path("py"), venv, need=True, run=run_with([0, 1]), environ=env) == 1 and str(lib) in env["LD_LIBRARY_PATH"]
    with pytest.raises(RuntimeError, match="does not import"):
        nbkit.ensure_tf_gpu(Path("py"), venv, need=True, run=run_with([-1]), environ={})
    with pytest.raises(RuntimeError, match="sees NO GPU"):
        nbkit.ensure_tf_gpu(Path("py"), venv, need=True, run=run_with([0, 0]), environ={})
    assert nbkit.ensure_tf_gpu(Path("py"), tmp_path / "novenv", need=False, run=run_with([0]), environ={}) == 0


def test_runs_budget_and_output_guard(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    with pytest.raises(FileNotFoundError):
        nbkit.latest_run(runs, "c")
    for rid, status in (("20261011T010000Z_c_aa", "completed"), ("20261011T020000Z_c_bb", "failed"), ("20261011T030000Z_c_cc", "dry_run")):
        (runs / rid).mkdir(parents=True)
        (runs / rid / "run.json").write_text(json.dumps({"status": status}), encoding="utf-8")
    assert nbkit.latest_run(runs, "c").parent.name == "20261011T020000Z_c_bb"
    assert nbkit.remaining_hours(11.0, 0.0, 1.0, now=lambda: 3600.0) == 9.0
    assert nbkit.remaining_hours(1.0, 0.0, 1.0, now=lambda: 7200.0) == 0.25
    out = tmp_path / "outputs"
    (out / "a").mkdir(parents=True)
    (out / "a" / "report.json").write_text("{}", encoding="utf-8")
    (out / "a" / "download_x.zip").write_bytes(b"PK\x03\x04")
    assert nbkit.forbidden_outputs(out) == [] and nbkit.forbidden_outputs(tmp_path / "none") == []
    assert any("UNDUH INI" in ln for ln in nbkit.describe_outputs(out)) and nbkit.describe_outputs(tmp_path / "none") == []
    (out / "a" / "x.npy").write_bytes(b"\x93NUMPY")
    (out / "a" / "hidden.bin").write_bytes(jpeg_bytes())
    assert len(nbkit.forbidden_outputs(out)) == 2


def test_child_env_strips_kernel_paths_for_foreign_interpreters() -> None:
    base = {"PYTHONPATH": "/yolox", "PYTHONHOME": "/h", "PATH": "/bin"}
    assert nbkit.child_env(sys.executable, None, base)["PYTHONPATH"] == "/yolox"
    venv = nbkit.child_env("/tmp/venv-export/bin/python", {"X": "1"}, base)
    assert "PYTHONPATH" not in venv and "PYTHONHOME" not in venv and venv["X"] == "1" and venv["PATH"] == "/bin"


def test_dir_size_mb_counts_files_recursively(tmp_path: Path) -> None:
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "x.bin").write_bytes(b"\0" * (3 << 20))
    (tmp_path / "y.bin").write_bytes(b"\0" * (2 << 20))
    assert nbkit.dir_size_mb(tmp_path) == 5
    assert nbkit.dir_size_mb(tmp_path / "missing") == 0


def test_child_env_forces_agg_matplotlib_backend_for_venv_programs() -> None:
    """Kaggle kernels set MPLBACKEND=module://matplotlib_inline.backend_inline; a venv without matplotlib_inline then
    fails `import matplotlib.pyplot` (mediapipe imports it)."""
    base = {"MPLBACKEND": "module://matplotlib_inline.backend_inline", "PATH": "/bin"}
    assert nbkit.child_env("/tmp/venv-mm/bin/python", None, base)["MPLBACKEND"] == "Agg"
    assert nbkit.child_env(sys.executable, None, base)["MPLBACKEND"] == base["MPLBACKEND"]  # the kernel keeps its own


def test_child_env_puts_venv_bin_on_path(tmp_path: Path) -> None:
    (tmp_path / "venv" / "bin").mkdir(parents=True)
    (tmp_path / "venv" / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    exe = str(tmp_path / "venv" / "bin" / "onnx2tf")
    env = nbkit.child_env(exe, None, {"PATH": "/usr/bin", "PYTHONPATH": "/k"})
    assert env["PATH"].split(os.pathsep)[0] == os.path.dirname(os.path.abspath(exe)) and "PYTHONPATH" not in env


def test_fetch_url_md5_and_counts(tmp_path: Path) -> None:
    op = FakeOpener(b"weights", fail=[urllib.error.URLError("x")])
    dest = nbkit.fetch_url(U, tmp_path / "w.pth", opener=op, sleep=NOSLEEP)
    assert dest.read_bytes() == b"weights" and nbkit.fetch_url(U, dest, opener=FakeOpener(b"other")) == dest
    with pytest.raises(nbkit.DataError, match="download of"):
        nbkit.fetch_url(U, tmp_path / "x.pth", opener=FakeOpener(b"", fail=[urllib.error.HTTPError("u", 403, "no", {}, None)]))  # type: ignore[arg-type]
    seen: list[int] = []
    assert nbkit.md5_file(dest, seen.append) == hashlib.md5(b"weights").hexdigest() and seen == [7]
    canon = tmp_path / "c.jsonl"
    canon.write_text(json.dumps({"image": "a", "boxes": [{"cls": "caries_primary"}]}) + "\n\n", encoding="utf-8")
    assert nbkit.box_counts(canon) == {"caries_primary": 1}


# ----------------------------------------------------------------- notebooks + simulation harness


def test_generated_notebooks_inline_bootstrap(tmp_path: Path) -> None:
    for name in make_ipynb.SOURCES:
        nb = json.loads((KAGGLE / name).with_suffix(".ipynb").read_text(encoding="utf-8"))
        cell1 = "".join(next(c for c in nb["cells"] if "".join(c["source"]).startswith("# 1."))["source"])
        assert "def acquire_code" in cell1 and "from bootstrap import" not in cell1
        compile(cell1, name, "exec")  # the inlined __future__ import stays a legal first statement
        settings = "".join(next(c for c in nb["cells"] if "".join(c["source"]).startswith("# 0."))["source"])
        assert "SMOKE_TEST = True" in settings and "CODE_REF = \"main\"" in settings
    with pytest.raises(ValueError, match="unterminated"):
        make_ipynb.inline_files(["# >>> inline: bootstrap.py", "x"], KAGGLE)


def test_harness_helpers(tmp_path: Path) -> None:
    cells = sim.load_cells(KAGGLE / "yolox_nano_kaggle.ipynb")
    assert [n for n, _ in cells][:3] == [0, 1, 2] and cells[-1][0] == 15
    assert "SMOKE_TEST = False" in sim.patch_settings(cells[0][1], smoke=False)
    with pytest.raises(ValueError, match="SMOKE_TEST"):
        sim.patch_settings("x = 1", smoke=True)
    bad_nb = tmp_path / "bad.ipynb"
    bad_nb.write_text(json.dumps({"cells": [{"cell_type": "code", "source": ["x = 1"]}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="title"):
        sim.load_cells(bad_nb)
    lock = tmp_path / "l.txt"
    lock.write_text("a==1\nnvidia-cudnn-cu12==8\nb==2\n", encoding="utf-8")
    assert sim.cpu_lock(lock, tmp_path / "o.txt").read_text(encoding="utf-8") == "a==1\nb==2\n"
    sim.install_fake_secret("T0K")
    from kaggle_secrets import UserSecretsClient

    assert UserSecretsClient().get_secret("GITHUB_TOKEN") == "T0K"
    with pytest.raises(KeyError):
        UserSecretsClient().get_secret("OTHER")
    del sys.modules["kaggle_secrets"]
    (tmp_path / "src" / ".git").mkdir(parents=True)
    (tmp_path / "src" / ".git" / "config").write_text("[remote] url = x", encoding="utf-8")
    assert sim.check_token_not_leaked(tmp_path, "nothing", "T0K") == []
    (tmp_path / "src" / ".git" / "config").write_text("token T0K", encoding="utf-8")
    assert len(sim.check_token_not_leaked(tmp_path, "T0K printed", "T0K")) == 2
    buf1, buf2 = io.StringIO(), io.StringIO()
    tee = sim.Tee(buf1, buf2)
    tee.write("x")
    tee.flush()
    assert buf1.getvalue() == buf2.getvalue() == "x"


def test_harness_range_server_and_layout_checks(tmp_path: Path) -> None:
    blob = os.urandom(5000)
    f = tmp_path / "Dataset.zip"
    f.write_bytes(blob)
    with sim.serve_file(f) as url:
        assert nbkit.fetch_range(url, 100, 199) == blob[100:200]
        with urllib.request.urlopen(url) as resp:  # no Range -> whole file, 200
            assert resp.status == 200 and resp.read() == blob
    split = tmp_path / "mp" / "train"
    (split / "images").mkdir(parents=True)
    (split / "images" / "a.jpg").write_bytes(jpeg_bytes(20, 10))
    from PIL import Image

    Image.new("RGB", (20, 10)).save(split / "images" / "b.png")
    (split / "images" / "b.jpg").write_bytes((split / "images" / "b.png").read_bytes())
    (split / "images" / "b.png").unlink()
    labels = {"categories": [{"id": 1, "name": "caries_permanent"}, {"id": 2, "name": "caries_primary"}],
              "images": [{"id": 1, "file_name": "a.jpg", "width": 20, "height": 10}, {"id": 2, "file_name": "b.jpg", "width": 20, "height": 10}],
              "annotations": [{"id": 1, "image_id": 1, "category_id": 1, "bbox": [1, 1, 5, 5]}]}
    (split / "labels.json").write_text(json.dumps(labels), encoding="utf-8")
    probs = sim.check_mediapipe_layout(split, min_annotated=1)
    assert len(probs) == 1 and "PNG" in probs[0]
    labels["categories"].append({"id": 0, "name": "caries"})
    labels["annotations"].append({"id": 2, "image_id": 1, "category_id": 0, "bbox": [15, 1, 10, 5]})
    labels["images"].append({"id": 3, "file_name": "c.jpg", "width": 1, "height": 1})
    (split / "labels.json").write_text(json.dumps(labels), encoding="utf-8")
    probs = sim.check_mediapipe_layout(split, min_annotated=4)
    assert any("background" in p for p in probs) and any("outside" in p for p in probs) and any("missing image" in p for p in probs)
    assert any("annotated images" in p for p in probs) and any("unexpected categories" in p for p in probs)
    assert sim.check_mediapipe_layout(tmp_path / "nope", min_annotated=1)[0].endswith("labels.json")
    yd = tmp_path / "yx"
    assert sim.check_yolox_layout(yd, "train")[0].startswith("missing")
    (yd / "annotations").mkdir(parents=True)
    (yd / "annotations" / "instances_train.json").write_text(json.dumps({"images": [{"file_name": "z.jpg"}], "categories": [{"id": 0}]}),
                                                             encoding="utf-8")
    assert len(sim.check_yolox_layout(yd, "train")) == 2


def test_lock_files_keep_pkg_resources() -> None:
    """setuptools >= 81 has no pkg_resources; tensorflow-hub/tensorboard import it (Kaggle run 2026-10-12)."""
    locks = Path(__file__).resolve().parents[1] / "kaggle" / "requirements"
    found = list(locks.glob("*.txt"))
    assert found
    for lock in found:
        text = lock.read_text(encoding="utf-8")
        m = re.search(r"^setuptools==([0-9]+)[.]", text, re.M)
        assert m is not None, f"{lock.name} must pin setuptools"
        if lock.name.startswith("mm_"):  # Model Maker: tensorflow-hub / tensorboard need pkg_resources
            assert int(m.group(1)) <= 80, f"{lock.name} must pin setuptools <= 80.x"
        elif "onnx2tf==2.6.9" in text:  # onnx2tf 2.6.9 requires setuptools==81.0.0 exactly (uv resolution error 2026-10-12)
            assert "setuptools==81.0.0" in text, f"{lock.name}: onnx2tf 2.6.9 needs setuptools==81.0.0"


class _Res:
    def __init__(self, rc: int, err: str = "") -> None:
        self.returncode, self.stderr, self.stdout = rc, err, ""


def _fake_run(cmd: list[str], **_: object) -> _Res:
    if cmd[-1] == "import ok":
        return _Res(0)
    return _Res(1, "Traceback (most recent call last):\nModuleNotFoundError: No module named 'pkg_resources'")


def test_import_failures_reports_last_error_line(tmp_path: Path) -> None:
    bad = nbkit.import_failures(tmp_path / "python", ["ok", "pkg_resources"], run=_fake_run)
    assert list(bad) == ["pkg_resources"]
    assert "No module named 'pkg_resources'" in bad["pkg_resources"]
    with pytest.raises(RuntimeError, match=re.escape("setuptools==80.9.0")):
        nbkit.require_imports(tmp_path / "python", ["pkg_resources"], run=_fake_run)
    nbkit.require_imports(tmp_path / "python", ["ok"], run=_fake_run)
