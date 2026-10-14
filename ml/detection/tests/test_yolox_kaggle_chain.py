"""YOLOX Kaggle path: trainer adapter run control (budget stop, hidden-crash detection, resume files, env),
nbkit kernel installs / pinned checkout / verified download, the ONNX -> TFLite fallback chain (faked onnx2tf and
parity tool), export.py precision + size gate, and the cells after training on a model with ZERO detections."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import jsonschema
import numpy as np
import pytest

import eval as ev
import export as ex
import nbkit
import predict as pr
import preprocess as pp
import thresholds as th
from bake_off import compare as cmp
from canonical import Box, ImageAnnotation, write_jsonl
from trainers import yolox_adapter as ya
from trainers.base import TrainContext

DET = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------------- adapter: Exp + env


def _ctx(tmp_path: Path, cfg: dict[str, Any], **opts: Any) -> TrainContext:
    c = json.loads(json.dumps(cfg))
    c.setdefault("trainer_options", {}).update(opts)
    return TrainContext(cfg=c, run_dir=tmp_path, canonical=tmp_path, splits=tmp_path, images_root=None, seed=3)


def test_exp_mirrors_nano_and_guards_eval(tmp_path: Path, config_dict: dict[str, Any]) -> None:
    text = ya.render_exp(_ctx(tmp_path, config_dict), tmp_path / "d")
    compile(text, "exp.py", "exec")
    for needle in ("self.depth = 0.33", "self.width = 0.25", "self.mosaic_prob = 0.5", "self.enable_mixup = False",
                   "depthwise=True", 'json_file=self.train_ann, name="train"', 'json_file=self.val_ann, name="val"',
                   'self.train_ann = "instances_train.json"', 'self.val_ann = "instances_val.json"', "self.num_classes = 2",
                   'setdefault("info", {})', 'delattr(layers, "COCOeval_opt")'):
        assert needle in text, needle


def test_exp_disables_jit_cocoeval_and_restores_info(tmp_path: Path, config_dict: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Execute the generated Exp against a fake ``yolox`` package (the real one is exercised in local_simulate)."""
    import types

    layers = types.ModuleType("yolox.layers")
    layers.COCOeval_opt = object  # type: ignore[attr-defined]
    exp_mod = types.ModuleType("yolox.exp")

    class BaseExp:
        def __init__(self) -> None:
            self.flip_prob, self.hsv_prob = 0.5, 1.0

    exp_mod.Exp = BaseExp  # type: ignore[attr-defined]
    data_mod = types.ModuleType("yolox.data")

    class FakeCOCO:
        def __init__(self) -> None:
            self.dataset: dict[str, Any] = {"images": []}  # YOLOX popped "info"

    class COCODataset:
        def __init__(self, **kw: Any) -> None:
            self.kw, self.coco = kw, FakeCOCO()

    data_mod.COCODataset = COCODataset  # type: ignore[attr-defined]
    data_mod.ValTransform = lambda legacy=False: ("val", legacy)  # type: ignore[attr-defined]
    data_mod.TrainTransform = lambda **kw: ("train", kw)  # type: ignore[attr-defined]
    pkg = types.ModuleType("yolox")
    pkg.layers, pkg.exp, pkg.data = layers, exp_mod, data_mod  # type: ignore[attr-defined]
    for name, mod in (("yolox", pkg), ("yolox.layers", layers), ("yolox.exp", exp_mod), ("yolox.data", data_mod)):
        monkeypatch.setitem(sys.modules, name, mod)
    ns: dict[str, Any] = {"__file__": str(tmp_path / "dentis_yolox_nano_exp.py")}
    exec(compile(ya.render_exp(_ctx(tmp_path, config_dict), tmp_path / "d"), "exp.py", "exec"), ns)
    assert not hasattr(layers, "COCOeval_opt")  # -> COCOEvaluator's `except ImportError` -> pycocotools COCOeval
    exp = ns["Exp"]()
    assert exp.exp_name == "dentis_yolox_nano_exp" and exp.data_dir == (tmp_path / "d").as_posix()
    val = exp.get_eval_dataset()
    assert val.coco.dataset["info"] == {} and val.kw["name"] == "val" and val.kw["json_file"] == "instances_val.json"
    train = exp.get_dataset()
    assert train.kw["name"] == "train" and train.kw["img_size"] == (416, 416)


def test_devices_and_environment(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert ya.effective_devices({}) == 1
    assert ya.effective_devices({"devices": 2}) == 1 and "ignored" in capsys.readouterr().out
    assert ya.effective_devices({"devices": 2, "allow_multi_gpu": True}) == 2
    with pytest.raises(ValueError, match="devices"):
        ya.effective_devices({"devices": 0})
    import os

    repo = tmp_path / "YOLOX"
    env = ya.train_environment({"PYTHONPATH": os.pathsep.join([str(repo), "/k"]), "MPLBACKEND": "module://matplotlib_inline.backend_inline"},
                               repo, 1)
    assert env["PYTHONPATH"].split(os.pathsep) == [str(repo), "/k"]  # no duplicate entry
    assert env["MPLBACKEND"] == "Agg" and env["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] == "1" and env["CUDA_VISIBLE_DEVICES"] == "0"
    assert ya.train_environment({"CUDA_VISIBLE_DEVICES": "1"}, repo, 1)["CUDA_VISIBLE_DEVICES"] == "1"
    assert ya.train_environment({}, repo, 2)["CUDA_VISIBLE_DEVICES"] == "0,1"


def test_build_command_launcher(tmp_path: Path, config_dict: dict[str, Any]) -> None:
    ctx = _ctx(tmp_path, config_dict)
    cmd = ya.build_command(Path("e.py"), ctx, ya.options(ctx), resume=None, python="py", launcher="shim.py")
    assert cmd[:3] == ["py", "shim.py", "tools/train.py"] and cmd[cmd.index("-d") + 1] == "1"


# ------------------------------------------------------------------------------ adapter: run control


def test_resume_files_and_caught_errors(tmp_path: Path) -> None:
    prev = tmp_path / "prev" / "dentis_yolox_nano_exp"
    prev.mkdir(parents=True)
    (prev / "latest_ckpt.pth").write_bytes(b"L")
    (prev / "best_ckpt.pth").write_bytes(b"B")
    exp_dir = tmp_path / "now"
    exp_dir.mkdir()
    assert ya.copy_resume_files(prev / "latest_ckpt.pth", exp_dir) == ["latest_ckpt.pth", "best_ckpt.pth"]
    assert (exp_dir / "best_ckpt.pth").read_bytes() == b"B"
    assert ya.copy_resume_files(exp_dir / "latest_ckpt.pth", exp_dir) == []  # same dir: nothing to copy
    log = tmp_path / "train_log.txt"
    assert ya.caught_errors(log) == []
    log.write_text("old session\n" + ya.CAUGHT_ERROR + " 'main' (old)\nKeyError: 'x'\n", encoding="utf-8")
    offset = log.stat().st_size
    with log.open("a", encoding="utf-8") as fh:
        fh.write("epoch 1 ok\n" + ya.CAUGHT_ERROR + " 'main', process 'MainProcess'\nTraceback ...\nKeyError: 'info'\n")
    errs = ya.caught_errors(log, offset)
    assert len(errs) == 1 and "KeyError: 'info'" in errs[0]
    assert len(ya.caught_errors(log)) == 2


def test_no_aug_epochs_and_stale_checkpoints(tmp_path: Path, config_dict: dict[str, Any]) -> None:
    assert [ya.no_aug_epochs(e) for e in (1, 2, 30, 150, 300)] == [0, 0, 3, 15, 15]
    cfg = json.loads(json.dumps(config_dict))
    cfg["train"]["epochs"] = 2
    assert "self.no_aug_epochs = 0" in ya.render_exp(_ctx(tmp_path, cfg), tmp_path / "d")  # smoke: epoch 1 = mosaic
    for n in ("latest_ckpt.pth", "best_ckpt.pth", "last_mosaic_epoch_ckpt.pth"):
        (tmp_path / n).write_bytes(b"old")
    (tmp_path / "train_log.txt").write_text("keep", encoding="utf-8")
    assert ya.clear_stale_checkpoints(tmp_path) == ["best_ckpt.pth", "last_mosaic_epoch_ckpt.pth", "latest_ckpt.pth"]
    assert [p.name for p in tmp_path.iterdir() if p.is_file()] == ["train_log.txt"]


def test_checkpoint_epoch(tmp_path: Path) -> None:
    p = tmp_path / "latest_ckpt.pth"
    assert ya.checkpoint_epoch(p) is None
    p.write_bytes(b"x")
    assert ya.checkpoint_epoch(p, load=lambda _: {"start_epoch": 7}) == 7

    def corrupt(_: Path) -> Any:
        raise EOFError("Ran out of input")

    assert ya.checkpoint_epoch(p, load=corrupt) is None


@pytest.mark.parametrize(
    ("kw", "expect"),
    [
        ({"rc": 0, "stopped": False, "errors": [], "epoch": 150}, "completed"),
        ({"rc": None, "stopped": True, "errors": [], "epoch": 40}, "time_budget_reached_resume_next_session"),
        ({"rc": None, "stopped": True, "errors": [], "epoch": 150}, "completed"),
        ({"rc": 0, "stopped": False, "errors": ["An error has been caught ... KeyError: 'info'"], "epoch": 10}, "crashed"),
        ({"rc": 0, "stopped": False, "errors": [], "epoch": 10}, "without a time-budget stop"),
        ({"rc": 0, "stopped": False, "errors": [], "epoch": None}, "no readable latest_ckpt"),
        ({"rc": 1, "stopped": False, "errors": [], "epoch": 3}, "exited with code 1"),
    ],
)
def test_outcome(kw: dict[str, Any], expect: str) -> None:
    if expect in ("completed", "time_budget_reached_resume_next_session"):
        assert ya.outcome(max_epoch=150, **kw) == expect
    else:
        with pytest.raises(RuntimeError, match=expect):
            ya.outcome(max_epoch=150, **kw)


class FakeProc:
    def __init__(self, finish_after: int | None, rc: int = 0) -> None:
        self.polls, self.finish_after, self.rc = 0, finish_after, rc
        self.terminated = self.killed = False
        self.pid = 4242

    def poll(self) -> int | None:
        self.polls += 1
        if self.terminated or self.killed:
            return -15
        return self.rc if self.finish_after is not None and self.polls > self.finish_after else None

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        return -15


def _budget_run(tmp_path: Path, proc: FakeProc, *, budget: float | None, touch_at: float | None = None,
                grace: float = 100.0, monkeypatch: pytest.MonkeyPatch | None = None) -> tuple[int | None, bool, list[float]]:
    watch = tmp_path / "latest_ckpt.pth"
    watch.write_bytes(b"0")
    t = [0.0]
    sleeps: list[float] = []

    def sleep(s: float) -> None:
        sleeps.append(s)
        t[0] += s
        if touch_at is not None and t[0] >= touch_at and watch.read_bytes() == b"0":
            import os

            watch.write_bytes(b"1")
            os.utime(watch, (watch.stat().st_mtime + 50, watch.stat().st_mtime + 50))

    if monkeypatch is not None:  # the fake process has no process group: use the Windows branch of _stop
        monkeypatch.setattr(sys, "platform", "win32")
    rc, stopped = ya.run_with_budget(["x"], cwd=tmp_path, env={}, budget_s=budget, watch=watch, grace_s=grace, settle_s=1.0,
                                     poll_s=5.0, popen=lambda *a, **k: proc, clock=lambda: t[0], sleep=sleep)
    return rc, stopped, sleeps


def test_run_with_budget_normal_exit(tmp_path: Path) -> None:
    rc, stopped, _ = _budget_run(tmp_path, FakeProc(finish_after=3, rc=0), budget=None)
    assert (rc, stopped) == (0, False)


def test_run_with_budget_stops_after_next_checkpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proc = FakeProc(finish_after=None)
    rc, stopped, sleeps = _budget_run(tmp_path, proc, budget=20.0, touch_at=40.0, monkeypatch=monkeypatch)
    assert (rc, stopped) == (None, True) and proc.terminated
    assert 1.0 in sleeps and sum(sleeps) < 100.0  # stopped at the checkpoint write, well before the grace period


def test_run_with_budget_grace_expires(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proc = FakeProc(finish_after=None)
    rc, stopped, sleeps = _budget_run(tmp_path, proc, budget=10.0, touch_at=None, grace=30.0, monkeypatch=monkeypatch)
    assert (rc, stopped) == (None, True) and proc.terminated and sum(sleeps) >= 40.0


def _clocked(t: list[float], on_sleep: Callable[[float], None] | None = None) -> Callable[[float], None]:
    def sleep(s: float) -> None:
        t[0] += s
        if on_sleep is not None:
            on_sleep(t[0])

    return sleep


def test_run_with_budget_kills_a_stalled_trainer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kaggle 2026-10-14: ~10 h of silence after the first iterations -> detect the hang in minutes, not at the budget."""
    monkeypatch.setattr(sys, "platform", "win32")
    log = tmp_path / "train_log.txt"
    log.write_text("epoch: 1/150, iter: 10/137\n", encoding="utf-8")
    proc = FakeProc(finish_after=None)
    t = [0.0]
    with pytest.raises(ya.TrainerStalled, match="epoch: 1/150"):
        ya.run_with_budget(["x"], cwd=tmp_path, env={}, budget_s=None, watch=tmp_path / "w", log=log, stall_s=60.0,
                           popen=lambda *a, **k: proc, clock=lambda: t[0], sleep=_clocked(t))
    assert proc.terminated and t[0] <= 70.0


def test_run_with_budget_prints_yolox_progress_and_tolerates_a_growing_log(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    log = tmp_path / "train_log.txt"
    log.write_text("start\n", encoding="utf-8")
    proc = FakeProc(finish_after=40)
    t = [0.0]

    def grow(now: float) -> None:
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"epoch: 1/150, iter: {int(now)}/137\n")

    rc, stopped = ya.run_with_budget(["x"], cwd=tmp_path, env={}, budget_s=None, watch=tmp_path / "w", log=log, stall_s=30.0,
                                     tail_every_s=20.0, popen=lambda *a, **k: proc, clock=lambda: t[0], sleep=_clocked(t, grow))
    assert (rc, stopped) == (0, False)
    assert "[yolox" in capsys.readouterr().out


def test_is_retriable_only_for_hangs() -> None:
    assert ya.is_retriable(ya.TrainerStalled("x"))
    assert ya.is_retriable(RuntimeError("YOLOX training crashed: RuntimeError: DataLoader timed out after 300 seconds"))
    assert ya.is_retriable(RuntimeError("DataLoader worker (pid 12) is killed by signal: Killed."))
    assert not ya.is_retriable(RuntimeError("CUDA out of memory"))
    assert not ya.is_retriable(ValueError("bad config"))


def test_supervise_restarts_after_a_hang_then_succeeds() -> None:
    calls: list[int] = []
    msgs: list[str] = []

    def run_once(attempt: int) -> str:
        calls.append(attempt)
        if attempt < 2:
            raise ya.TrainerStalled(f"hang {attempt}")
        return "completed"

    result, restarts = ya.supervise(run_once, max_restarts=2, log=msgs.append)
    assert result == "completed" and calls == [0, 1, 2]
    assert [r["attempt"] for r in restarts] == [0, 1] and "hang 0" in restarts[0]["reason"] and len(msgs) == 2


def test_supervise_gives_up_after_max_restarts_and_never_retries_real_errors() -> None:
    attempts: list[int] = []

    def always_hangs(attempt: int) -> str:
        attempts.append(attempt)
        raise ya.TrainerStalled("still hanging")

    with pytest.raises(ya.TrainerStalled):
        ya.supervise(always_hangs, max_restarts=1, log=lambda _m: None)
    assert attempts == [0, 1]

    seen: list[int] = []

    def crashes(attempt: int) -> str:
        seen.append(attempt)
        raise RuntimeError("CUDA out of memory")

    with pytest.raises(RuntimeError, match="CUDA"):
        ya.supervise(crashes, max_restarts=2, log=lambda _m: None)
    assert seen == [0]  # a genuine failure is not retried


def test_last_log_line(tmp_path: Path) -> None:
    assert ya.last_log_line(tmp_path / "missing.txt") == ""
    log = tmp_path / "l.txt"
    log.write_text("a\n\nlast progress line  \n\n", encoding="utf-8")
    assert ya.last_log_line(log) == "last progress line"
    log.write_text("", encoding="utf-8")
    assert ya.last_log_line(log) == ""


def test_run_with_budget_interrupt_kills_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proc = FakeProc(finish_after=None)
    monkeypatch.setattr(sys, "platform", "win32")

    def boom(_: float) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        ya.run_with_budget(["x"], cwd=tmp_path, env={}, budget_s=None, watch=tmp_path / "w", popen=lambda *a, **k: proc,
                           sleep=boom)
    assert proc.terminated


# ------------------------------------------------------------------------- nbkit: kernel + checkout


def test_venv_bin_and_kernel_pip_install(tmp_path: Path) -> None:
    import os

    assert nbkit.venv_bin(Path("/v/bin/python"), "onnx2tf").name == ("onnx2tf.exe" if os.name == "nt" else "onnx2tf")
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def run(cmd: Sequence[Any], **kw: Any) -> int:
        calls.append(([str(c) for c in cmd], kw))
        return 0

    assert nbkit.kernel_pip_install([], tmp=tmp_path, run=run) == 0 and calls == []
    nbkit.kernel_pip_install(["pycocotools==2.0.11"], tmp=tmp_path, run=run, versions={"torch": "2.6.0+cu124", "numpy": "2.2.6"})
    cmd, kw = calls[0]
    assert cmd[1:4] == ["-m", "pip", "install"] and cmd[-1] == "pycocotools==2.0.11" and kw["timeout"] == 1800.0
    cons = Path(cmd[cmd.index("-c") + 1]).read_text(encoding="utf-8")
    assert cons == "numpy==2.2.6\ntorch==2.6.0+cu124\n"  # torch can never be replaced by a dependency resolution
    assert set(nbkit.installed_versions(["pytest", "surely-not-installed-pkg"])) == {"pytest"}


def test_cpu_only_venv_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    (tmp_path / "venv" / "pyvenv.cfg").write_text("home = /usr", encoding="utf-8")
    monkeypatch.setattr(nbkit, "CPU_ONLY_BINDIRS", set())
    env = {"PATH": "/usr/bin", "PYTHONPATH": "/k", "LD_LIBRARY_PATH": "/usr/local/cuda/lib64"}
    assert "CUDA_VISIBLE_DEVICES" not in nbkit.child_env(str(venv_bin / "onnx2tf"), environ=env)
    nbkit.use_cpu_only(venv_bin / "python")
    got = nbkit.child_env(str(venv_bin / "onnx2tf"), environ=env)
    assert got["CUDA_VISIBLE_DEVICES"] == "-1" and "PYTHONPATH" not in got and got["MPLBACKEND"] == "Agg"
    assert "CUDA_VISIBLE_DEVICES" not in nbkit.child_env(sys.executable, environ=env, kernel=sys.executable)  # kernel keeps GPU


def test_ensure_kernel_modules(tmp_path: Path) -> None:
    state = {"pycocotools": "ImportError: numpy.core.multiarray failed to import"}
    installed: list[str] = []

    def failures(mods: Sequence[str]) -> dict[str, str]:
        return {m: state[m] for m in mods if m in state}

    def run(cmd: Sequence[Any], **kw: Any) -> int:
        installed.extend(str(c) for c in cmd if "==" in str(c) and "torch" not in str(c))
        state.clear()
        return 0

    mapping = {"pycocotools": "pycocotools==2.0.11", "loguru": "loguru==0.7.3"}
    assert nbkit.ensure_kernel_modules(mapping, tmp=tmp_path, run=run, failures=failures) == ["pycocotools==2.0.11"]
    assert "pycocotools==2.0.11" in installed
    assert nbkit.ensure_kernel_modules(mapping, tmp=tmp_path, run=run, failures=failures) == []
    with pytest.raises(RuntimeError, match="still not importable"):
        nbkit.ensure_kernel_modules(mapping, tmp=tmp_path, run=lambda *a, **k: 0, failures=lambda m: {"loguru": "x"})


def test_ensure_git_checkout(tmp_path: Path) -> None:
    sha = "6ddff4824372906469a7fae2dc3206c7aa4bbaee"
    dest = tmp_path / "YOLOX"
    heads: dict[str, str | None] = {"v": None}
    log: list[list[str]] = []

    def head(_: Path) -> str | None:
        return heads["v"]

    def run_ok(cmd: Sequence[Any], **kw: Any) -> int:
        log.append([str(c) for c in cmd])
        if "checkout" in log[-1]:
            heads["v"] = sha
        return 0

    assert nbkit.ensure_git_checkout("https://x/YOLOX.git", sha, dest, run=run_ok, head=head) == "fetched"
    assert any(c[3:6] == ["fetch", "--depth", "1"] and c[-1] == sha for c in log)
    assert nbkit.ensure_git_checkout("https://x/YOLOX.git", sha, dest, run=run_ok, head=head) == "reused"
    heads["v"], log[:] = "0" * 40, []

    def run_no_sha_fetch(cmd: Sequence[Any], **kw: Any) -> int:
        c = [str(x) for x in cmd]
        log.append(c)
        if "fetch" in c:
            return 128
        if "checkout" in c and sha in c:
            heads["v"] = sha
        return 0

    assert nbkit.ensure_git_checkout("https://x/YOLOX.git", sha, dest, run=run_no_sha_fetch, head=head) == "cloned"
    assert any(c[:3] == ["git", "clone", "--progress"] for c in log)
    heads["v"] = "f" * 40
    with pytest.raises(RuntimeError, match="expected"):
        nbkit.ensure_git_checkout("https://x/YOLOX.git", sha, dest, run=lambda *a, **k: 0, head=head)


def test_git_head(tmp_path: Path) -> None:
    assert nbkit.git_head(tmp_path) is None
    (tmp_path / ".git").mkdir()
    ok = subprocess.CompletedProcess([], 0, stdout="abc\n", stderr="")
    assert nbkit.git_head(tmp_path, run=lambda *a, **k: ok) == "abc"
    bad = subprocess.CompletedProcess([], 128, stdout="", stderr="fatal")
    assert nbkit.git_head(tmp_path, run=lambda *a, **k: bad) is None


def test_fetch_verified(tmp_path: Path) -> None:
    import hashlib

    def fetch(url: str, dest: Path) -> Path:
        dest.write_bytes(b"weights")
        return dest

    good = hashlib.sha256(b"weights").hexdigest()
    assert nbkit.fetch_verified("u", tmp_path / "w.pth", good, fetch=fetch).read_bytes() == b"weights"
    with pytest.raises(nbkit.DataError, match="sha256"):
        nbkit.fetch_verified("u", tmp_path / "w.pth", "0" * 64, fetch=fetch)
    assert not (tmp_path / "w.pth").exists()  # deleted -> re-running downloads again
    assert nbkit.size_gate(15 * 2**20, 15.0)["ok"] and not nbkit.size_gate(15 * 2**20 + 1, 15.0)["ok"]
    assert "!!!" in nbkit.banner(["x"])


# --------------------------------------------------------------------------- nbkit: conversion chain


class FakeTools:
    """Fake onnxsim / onnx2tf / check_int8_parity.py driven by per-attempt behaviour.

    ``behaviour[name]`` = (exit code, files written, {int8 kind: cosine or None (= interpreter fails)})."""

    def __init__(self, behaviour: dict[str, tuple[int | str, tuple[str, ...], dict[str, float | None]]], *, onnxsim_rc: int = 0,
                 conv_cosine: float = 0.9999) -> None:
        self.behaviour, self.onnxsim_rc, self.conv_cosine = behaviour, onnxsim_rc, conv_cosine
        self.calls: list[list[str]] = []
        self.current: dict[str, float | None] = {}

    def attempt_name(self, out_dir: str, int8: bool) -> str:
        return Path(out_dir).name.removeprefix("tflite_")

    def __call__(self, cmd: Sequence[Any], **kw: Any) -> int:
        c = [str(x) for x in cmd]
        self.calls.append(c)
        exe = Path(c[0]).stem
        if exe == "onnxsim":
            if self.onnxsim_rc == 0:
                Path(c[2]).write_bytes(b"onnx")
            return self.onnxsim_rc
        if exe == "onnx2tf":
            out = Path(c[c.index("-o") + 1])
            name = out.name.removeprefix("tflite_")
            rc, files, cos = self.behaviour[name]
            out.mkdir(parents=True, exist_ok=True)
            for f in files:
                (out / f"m_{f}.tflite").write_bytes(b"\x1c\x00\x00\x00TFL3" + b"\x00" * 64)
            self.current.update({str(out / f"m_{k}.tflite"): v for k, v in cos.items()})
            if rc == "timeout":
                raise subprocess.TimeoutExpired(c, 1)
            assert isinstance(rc, int)
            return rc
        if "check_int8_parity.py" in c:
            other = c[c.index("--int8-model") + 1]
            float_model = c[c.index("--float-model") + 1]
            if float_model.endswith(".onnx"):
                cosv: float | None = self.conv_cosine
            else:
                cosv = self.current.get(other.replace("integer_quant", "integer_quant"), 0.999)
                key = {"_integer_quant": "integer_quant", "_full_integer_quant": "full_integer_quant", "_float16": "float16"}
                for suffix, k in key.items():
                    if other.endswith(suffix + ".tflite"):
                        cosv = self.current.get(str(Path(other).parent / f"m_{k}.tflite"), 0.999)
            if cosv is None:
                return 1  # interpreter failed: no report
            rep = Path(c[c.index("--report") + 1])
            rep.write_text(json.dumps({"passed": cosv >= 0.98, "tensors": [{"index": 0, "cosine": cosv}],
                                       "failures": [] if cosv >= 0.98 else [f"tensor 0: cosine {cosv} < 0.98"]}), encoding="utf-8")
            return 0 if cosv >= 0.98 else 1
        raise AssertionError(f"unexpected command {c}")


ALL = ("float32", "float16", "integer_quant", "full_integer_quant")


def _chain(tmp_path: Path, tools: FakeTools) -> dict[str, Any]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    onnx = tmp_path / "m.onnx"
    onnx.write_bytes(b"onnx")
    return nbkit.tflite_chain(onnx=onnx, calib=tmp_path / "c.npy", samples=tmp_path / "s.npy", out_root=tmp_path / "export",
                              report_dir=tmp_path / "out", export_py=tmp_path / "venv" / "bin" / "python", det=DET, run=tools,
                              log=lambda s: None)


def test_chain_tf_converter_int8_ok(tmp_path: Path) -> None:
    tools = FakeTools({"tf_converter": (0, ALL, {"integer_quant": 0.995, "full_integer_quant": 0.99})})
    rec = _chain(tmp_path, tools)
    assert rec["precision"] == "int8" and rec["int8_ok"] is True and rec["model"].endswith("m_integer_quant.tflite")
    assert rec["int8_method"] == "onnx2tf tf_converter int8_float_io" and rec["onnx_simplified"] is True
    assert [a["name"] for a in rec["attempts"]] == ["tf_converter"]  # a passing INT8 ends the search
    onnx2tf_cmd = next(c for c in tools.calls if Path(c[0]).stem == "onnx2tf")
    assert onnx2tf_cmd[onnx2tf_cmd.index("-i") + 1].endswith("m_sim.onnx")
    assert onnx2tf_cmd[onnx2tf_cmd.index("-cind") + 1:onnx2tf_cmd.index("-cind") + 5][2:] == list(nbkit.CALIB_MEAN_STD)
    assert rec["conversion_parity"]["passed"] is True
    saved = json.loads((tmp_path / "out" / "conversion.json").read_text(encoding="utf-8"))
    assert saved["int8_ok"] is True and all("_cos" not in c for c in saved["candidates"])


def test_chain_failed_run_files_ignored_and_failing_parity_kept(tmp_path: Path) -> None:
    """tf_converter crashes but leaves files (never used); flatbuffer_direct works with BAD parity -> kept, int8_ok False."""
    tools = FakeTools({"tf_converter": (1, ALL, {"integer_quant": 0.999}),
                       "flatbuffer_direct": (0, ALL, {"integer_quant": 0.45, "full_integer_quant": 0.60}),
                       "flatbuffer_direct_per_tensor": (1, ALL, {"integer_quant": -0.02})})
    rec = _chain(tmp_path, tools)
    assert rec["precision"] == "int8" and rec["int8_ok"] is False
    assert rec["model"].endswith("m_full_integer_quant.tflite") and "PARITY FAILED" in rec["int8_method"]
    assert [a["rc"] for a in rec["attempts"]] == [1, 0, 1]
    assert {c["attempt"] for c in rec["candidates"]} == {"flatbuffer_direct"}
    # equal cosine (to 4 decimals; local run 2026-10-12: 0.96187739549 vs 0.96187739553) -> float I/O wins
    tie = FakeTools({"tf_converter": (0, ALL, {"integer_quant": 0.961877395, "full_integer_quant": 0.961877396}),
                     "flatbuffer_direct": (1, (), {}), "flatbuffer_direct_per_tensor": (1, (), {})})
    assert _chain(tmp_path / "tie", tie)["model"].endswith("m_integer_quant.tflite")


def test_chain_float_fallbacks(tmp_path: Path) -> None:
    # every INT8 attempt fails -> a float-only run -> float16
    tools = FakeTools({"tf_converter": (1, (), {}), "flatbuffer_direct": ("timeout", (), {}),
                       "flatbuffer_direct_per_tensor": (1, ("float32",), {}), "tf_converter_float": (0, ("float32", "float16"), {})})
    rec = _chain(tmp_path, tools)
    assert rec["precision"] == "float16" and rec["int8_ok"] is False and rec["model"].endswith("m_float16.tflite")
    assert rec["attempts"][1]["rc"] == "timeout" and rec["int8_method"].startswith("none")
    # INT8 exists but the interpreter cannot run it -> float16 of that SAME successful run, no extra conversion
    tools2 = FakeTools({"tf_converter": (0, ALL, {"integer_quant": None, "full_integer_quant": None}),
                        "flatbuffer_direct": (1, (), {}), "flatbuffer_direct_per_tensor": (1, (), {})})
    rec2 = _chain(tmp_path / "b", tools2)
    assert rec2["precision"] == "float16" and "tf_converter_float" not in [a["name"] for a in rec2["attempts"]]
    # no float16 at all -> float32
    tools3 = FakeTools({"tf_converter": (1, (), {}), "flatbuffer_direct": (1, (), {}), "flatbuffer_direct_per_tensor": (1, (), {}),
                        "tf_converter_float": (1, (), {}), "flatbuffer_direct_float": (0, ("float32",), {})}, onnxsim_rc=1)
    rec3 = _chain(tmp_path / "c", tools3)
    assert rec3["precision"] == "float32" and rec3["onnx_simplified"] is False and rec3["model"].endswith("m_float32.tflite")


def test_chain_total_failure_raises(tmp_path: Path) -> None:
    tools = FakeTools({n: (1, (), {}) for n in ("tf_converter", "flatbuffer_direct", "flatbuffer_direct_per_tensor",
                                                 "tf_converter_float", "flatbuffer_direct_float")})
    with pytest.raises(RuntimeError, match="gagal total"):
        _chain(tmp_path, tools)
    assert json.loads((tmp_path / "out" / "conversion.json").read_text(encoding="utf-8"))["precision"] is None


def test_chain_ignores_stale_outputs(tmp_path: Path) -> None:
    stale = tmp_path / "export" / "tflite_tf_converter"
    stale.mkdir(parents=True)
    (stale / "old_integer_quant.tflite").write_bytes(b"old")
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "parity_tensor_tf_converter_int8_float_io.json").write_text('{"passed": true}', encoding="utf-8")
    tools = FakeTools({"tf_converter": (1, (), {}), "flatbuffer_direct": (1, (), {}), "flatbuffer_direct_per_tensor": (1, (), {}),
                       "tf_converter_float": (0, ("float32",), {})})
    rec = _chain(tmp_path, tools)
    assert rec["precision"] == "float32" and not (stale / "old_integer_quant.tflite").exists()
    assert not (tmp_path / "out" / "parity_tensor_tf_converter_int8_float_io.json").exists()


# ------------------------------------------------------------------------------ export: precision + size


def test_export_precision_and_size_gate(tmp_path: Path, fake_tflite: Callable[[int], Path], write_cfg: Callable[..., Path],
                                        capsys: pytest.CaptureFixture[str]) -> None:
    cfg = write_cfg()
    lab = ex.build_bundle(cfg, tmp_path / "0.1.0-dev.1", tflite_in=fake_tflite(4096), allow_unverified_license=True,
                          precision="float16", size_gate="report", max_size_mb=0.001)
    assert lab["model"]["int8"] is False and lab["_size_gate"]["ok"] is False and lab["_size_gate"]["mode"] == "report"
    assert "size gate, report only" in capsys.readouterr().err
    assert "_size_gate" not in json.loads((tmp_path / "0.1.0-dev.1" / "labels.json").read_text(encoding="utf-8"))
    assert ex.verify_bundle(tmp_path / "0.1.0-dev.1", max_size_mb=None) == []
    with pytest.raises(ex.ExportError, match="size gate"):
        ex.build_bundle(cfg, tmp_path / "0.1.0-dev.2", tflite_in=fake_tflite(4096), allow_unverified_license=True, max_size_mb=0.001)
    for kw, msg in (({"precision": "int4"}, "precision"), ({"size_gate": "maybe"}, "size_gate"),
                    ({"precision": "float16", "release": True}, "--release needs")):
        with pytest.raises(ex.ExportError, match=msg):
            ex.build_bundle(cfg, tmp_path / "0.1.0-dev.3", tflite_in=fake_tflite(64), allow_unverified_license=True, **kw)
    blockers = ex.release_blockers(*_cfg_decision(cfg), lab)
    assert any("not INT8" in b for b in blockers)
    out = tmp_path / "0.1.0-dev.4"
    args = ["--cfg", str(cfg), "--format", "tflite", "--precision", "float32", "--size-gate", "report", "--max-size-mb", "0.001",
            "--out", str(out), "--tflite-in", str(fake_tflite(4096)), "--allow-unverified-license"]
    assert ex.main(args) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["int8"] is False and summary["size_gate"]["ok"] is False
    assert ex.main(["verify-bundle", str(out), "--max-size-mb", "0.001", "--size-gate", "report"]) == 0
    assert json.loads(capsys.readouterr().out)["size_gate_warnings"]
    assert ex.main(["verify-bundle", str(out), "--max-size-mb", "0.001"]) == 1


def _cfg_decision(cfg_path: Path) -> tuple[dict[str, Any], Any]:
    from license_gate import evaluate_gate, load_gate
    from train import load_config

    cfg, _ = load_config(cfg_path)
    return cfg, evaluate_gate(load_gate(), candidate=cfg["candidate"], datasets=cfg["data"]["datasets"],
                              cfg_license_verified=False, allow_unverified=True)


# --------------------------------------------- cells 11-14 on a smoke model with ZERO / too many detections


def _data(tmp_path: Path, make_image: Callable[..., Path]) -> dict[str, Path]:
    recs, assign = [], {}
    for i in range(8):
        name = f"img/{i}.png"
        make_image(tmp_path / "root" / name, w=40, h=20)
        cls = "caries_permanent" if i % 2 == 0 else "caries_primary"
        recs.append(ImageAnnotation(name, 40, 20, [Box(cls, 4.0, 4.0, 12.0, 12.0)] if i % 4 != 3 else [], source="ahmed2025",
                                    patient_id=f"p{i // 2}"))
        assign[name] = ("train", "train", "val", "val", "val", "test", "test", "test")[i]
    write_jsonl(recs, tmp_path / "canonical.jsonl")
    (tmp_path / "splits.json").write_text(json.dumps({"images": assign}), encoding="utf-8")
    (tmp_path / "meta.csv").write_text("image,patient_id,source\n" + "".join(f"img/{i}.png,p{i // 2},ahmed2025\n" for i in range(8)),
                                       encoding="utf-8")
    return {"gt": tmp_path / "canonical.jsonl", "splits": tmp_path / "splits.json", "root": tmp_path / "root", "meta": tmp_path / "meta.csv"}


@pytest.mark.parametrize("mode", ["zero", "flood"])
def test_cells_after_training_tolerate_degenerate_models(tmp_path: Path, make_image: Callable[..., Path],
                                                          fake_tflite: Callable[[int], Path], write_cfg: Callable[..., Path],
                                                          mode: str) -> None:
    """Smoke-run model: no detection at all (logits -30) or every one of the 3549 cells firing (logits 0 -> score 0.25)."""
    d = _data(tmp_path, make_image)
    labels = json.loads(json.dumps(ex.build_labels(_cfg_decision(write_cfg())[0], _cfg_decision(write_cfg())[1], version="0.1.0",
                                                   model_sha256="a" * 64, size_bytes=1, created_at="x")))
    raw = np.full((1, 3549, 7), -30.0 if mode == "zero" else 0.0)
    raw[0, :, :4] = 0.0
    out = tmp_path / "out"
    for split in ("val", "test"):
        rows = pr.predict_records(lambda x: [raw], labels, pp.split_records(d["gt"], d["splits"], split), d["root"], max_detections=100)
        assert all((len(r["boxes"]) == 0) == (mode == "zero") for r in rows)
        pr.write_jsonl(rows, out / f"preds_int8_{split}.jsonl")
    thr = out / "thresholds.json"
    assert th.main(["--pred", str(out / "preds_int8_val.jsonl"), "--gt", str(d["gt"]), "--splits", str(d["splits"]),
                    "--target-sensitivity", "0.85", "--out", str(thr)]) == 0
    t = json.loads(thr.read_text(encoding="utf-8"))
    assert all(0.0 < t[c] <= 1.0 for c in ("caries_permanent", "caries_primary"))
    schema = json.loads((DET / "report.schema.json").read_text(encoding="utf-8"))
    for split in ("val", "test"):
        rep = out / f"report_int8_{split}.json"
        assert ev.main(["--gt", str(d["gt"]), "--splits", str(d["splits"]), "--split", split, "--pred", str(out / f"preds_int8_{split}.jsonl"),
                        "--metadata", str(d["meta"]), "--thresholds", str(thr), "--bootstrap", "50", "--report", str(rep)]) == 0
        report = json.loads(rep.read_text(encoding="utf-8"))
        jsonschema.validate(report, schema)
        if mode == "zero":
            assert report["overall"]["per_class"]["any_caries"]["image_level"]["tp"] == 0
    lab = ex.build_bundle(write_cfg(), tmp_path / "0.1.0-dev.2", tflite_in=fake_tflite(256), allow_unverified_license=True,
                          thresholds_path=thr, eval_report=out / "report_int8_test.json", precision="float16", size_gate="report")
    assert lab["thresholds"]["status"] == "set_on_val"
    meta = {"yolox_nano": {"int8_size_mb": None, "model_size_mb": 1.8, "precision": "float16", "int8_ok": False, "parity_passed": None}}
    md = cmp.render_markdown({"yolox_nano": json.loads((out / "report_int8_test.json").read_text(encoding="utf-8"))}, meta)
    assert "belum diukur" in md
