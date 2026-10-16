"""Google Colab support: platform detection, Drive mounting, platform-specific preflight messages, and the Drive mirror
(restore a dropped session, never copy photos, never replace a good checkpoint with a half-written one).

Everything runs on tmp dirs; Drive and the Colab pop-up are faked."""

from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import bootstrap as bs
import nbkit

NOW = 1_000_000.0


def fake_run(stdout: str = "") -> Callable[..., Any]:
    def run(cmd: list[str], **kw: Any) -> Any:
        return SimpleNamespace(returncode=0 if stdout else 1, stdout=stdout, stderr="")

    return run


# ------------------------------------------------------------------------------------ platform + dirs


def test_compute_env_prefers_the_real_platform_marker() -> None:
    assert bs.compute_env({"KAGGLE_KERNEL_RUN_TYPE": "Interactive", "COLAB_GPU": "1"}, []) == "kaggle"
    assert bs.compute_env({"KAGGLE_USERNAME": "me", "COLAB_RELEASE_TAG": "x"}, []) == "colab"  # stray Kaggle key on a Colab VM
    assert bs.compute_env({"KAGGLE_URL_BASE": "x"}, []) == "kaggle"
    assert bs.compute_env({}, ["google.colab"]) == "colab" and bs.compute_env({}, []) == "local"


def test_colab_dirs_keep_work_local_and_persist_on_drive() -> None:
    d = bs.default_dirs("colab", environ={})
    assert d["work"].as_posix() == "/content/dentisai_outputs" and d["tmp"].as_posix() == "/content/tmp"
    assert d["persist"].as_posix() == "/content/drive/MyDrive/dentisai_runs"
    assert "persist" not in bs.default_dirs("kaggle", environ={})
    assert bs.default_dirs("kaggle", environ={"DENTISAI_PERSIST": "/p"})["persist"] == Path("/p")


def test_mount_drive(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "drive"
    assert bs.mount_drive(lambda p: (Path(p) / "MyDrive").mkdir(parents=True), root, is_mount=lambda p: False) is True
    assert bs.mount_drive(lambda p: pytest.fail("already mounted"), root, is_mount=lambda p: True) is True

    def refused(path: str) -> None:
        raise RuntimeError("Mountpoint must not already contain files")

    assert bs.mount_drive(refused, tmp_path / "other", is_mount=lambda p: False) is False
    assert "Google Drive tidak terpasang: RuntimeError" in capsys.readouterr().out
    assert bs.mount_drive(lambda p: None, tmp_path / "nomy", is_mount=lambda p: False) is False  # mounted but no MyDrive
    assert bs.mount_drive(None, tmp_path / "x", is_mount=lambda p: False) is False  # not a Colab kernel: no google.colab


def test_resolve_dirs(capsys: pytest.CaptureFixture[str]) -> None:
    assert "persist" in bs.resolve_dirs("colab", require_persist=True, environ={}, mount=lambda: True)
    assert bs.resolve_dirs("kaggle", require_persist=True, environ={}, mount=lambda: pytest.fail("no Drive on Kaggle"))["work"]
    local = bs.resolve_dirs("colab", require_persist=True, environ={"DENTISAI_PERSIST": "/elsewhere"}, mount=lambda: pytest.fail("not Drive"))
    assert local["persist"] == Path("/elsewhere")  # a persist dir outside /content/drive needs no mount
    with pytest.raises(SystemExit, match="butuh Google Drive"):
        bs.resolve_dirs("colab", require_persist=True, environ={}, mount=lambda: False)
    dirs = bs.resolve_dirs("colab", require_persist=False, environ={}, mount=lambda: False)  # smoke test still runs
    assert "persist" not in dirs and "PERINGATAN (uji cepat)" in capsys.readouterr().out


# ------------------------------------------------------------------------------------ messages


def test_preflight_messages_follow_the_platform(tmp_path: Path) -> None:
    base: dict[str, Any] = {"need_gpu": True, "hosts": [("a", 1)], "disk_paths": {"tmp": tmp_path, "drive": tmp_path},
                            "min_free_gb": 0.001, "run": fake_run(), "reach": lambda h, p: True, "version": (3, 12, 1)}
    with pytest.raises(SystemExit, match=r"Runtime -> Change runtime type -> Hardware accelerator: T4 GPU"):
        bs.preflight(**base, env="colab")
    with pytest.raises(SystemExit, match=r"Settings -> Accelerator"):
        bs.preflight(**base, env="kaggle")
    with pytest.raises(SystemExit, match=r"Colab biasanya selalu online"):
        bs.preflight(**{**base, "run": fake_run("Tesla T4, 15360 MiB\n"), "reach": lambda h, p: False}, env="colab")
    with pytest.raises(SystemExit, match=r"Disconnect and delete runtime"):
        bs.preflight(**{**base, "run": fake_run("Tesla T4, 15360 MiB\n"), "version": (3, 9, 0)}, env="colab")
    with pytest.raises(SystemExit, match=r"drive \([\d.]+ GB, butuh >= 1000000000\.0 GB\).*Kosongkan Google Drive"):
        bs.preflight(**{**base, "run": fake_run("Tesla T4, 15360 MiB\n"), "min_free_gb": {"tmp": 0.001, "drive": 1e9}}, env="colab")


def test_per_path_disk_minimum_checks_only_the_named_paths(tmp_path: Path) -> None:
    ok = bs.preflight(need_gpu=True, hosts=[], disk_paths={"tmp": tmp_path, "drive": tmp_path}, min_free_gb={"tmp": 0.001},
                      env="colab", run=fake_run("Tesla T4, 15360 MiB\n"), version=(3, 12, 1))
    assert set(ok["free_disk_gb"]) == {"tmp", "drive"}  # "drive" has no minimum -> not a problem


def test_token_help_and_missing_code_follow_the_platform(tmp_path: Path) -> None:
    colab, kaggle = "\n".join(bs.token_help("colab")), "\n".join(bs.token_help("kaggle"))
    assert "Notebook access" in colab and "Add-ons -> Secrets" not in colab
    assert "Add-ons -> Secrets" in kaggle and "Notebook access" not in kaggle
    with pytest.raises(SystemExit) as ei:
        bs.acquire_code(roots=[tmp_path / "none"], tmp=tmp_path / "t", ref="main", secret=lambda: None, env="colab")
    text = str(ei.value)
    assert "secret Colab" in text and "dentisai_inputs" in text and "Kaggle Secret" not in text


# ------------------------------------------------------------------------------------ the Drive mirror


def write(path: Path, data: bytes = b"x", age_s: float = 3600.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    t = time.time() - age_s
    os.utime(path, (t, t))
    return path


def test_sync_tree_copies_changed_files_once_and_skips_photos(tmp_path: Path) -> None:
    src, dst = tmp_path / "work", tmp_path / "drive"
    write(src / "yolox_outputs" / "latest_ckpt.pth", b"epoch1")
    write(src / "out" / "report.json", b"{}")
    write(src / "photo.jpg", b"\xff\xd8\xff")
    write(src / "calib.npy", b"\x93NUMPY")
    write(src / "run_signature.json", b"{}")
    seen: dict[str, tuple[int, int]] = {}
    first = nbkit.sync_tree(src, dst, seen=seen, skip_names=("run_signature.json",))
    assert sorted(first["copied"]) == ["out/report.json", "yolox_outputs/latest_ckpt.pth"] and first["errors"] == []
    assert not (dst / "photo.jpg").exists() and not (dst / "calib.npy").exists() and not (dst / "run_signature.json").exists()
    assert nbkit.sync_tree(src, dst, seen=seen, skip_names=("run_signature.json",))["copied"] == []  # unchanged -> not copied again
    write(src / "yolox_outputs" / "latest_ckpt.pth", b"epoch2", age_s=7200)  # same name + size, newer content (overwritten each epoch)
    again = nbkit.sync_tree(src, dst, seen=seen)
    assert "yolox_outputs/latest_ckpt.pth" in again["copied"]
    assert (dst / "yolox_outputs" / "latest_ckpt.pth").read_bytes() == b"epoch2" and not list(dst.rglob("*.part"))


def test_sync_tree_leaves_a_file_that_is_still_being_written(tmp_path: Path) -> None:
    src, dst = tmp_path / "work", tmp_path / "drive"
    write(dst / "latest_ckpt.pth", b"good epoch 7")
    write(src / "latest_ckpt.pth", b"half writ", age_s=1.0)  # modified 1 s ago
    res = nbkit.sync_tree(src, dst, settle_s=20.0)
    assert res["unsettled"] == ["latest_ckpt.pth"] and res["copied"] == []
    assert (dst / "latest_ckpt.pth").read_bytes() == b"good epoch 7"  # the good copy on Drive is untouched
    assert nbkit.sync_tree(src, dst, settle_s=0.0)["copied"] == ["latest_ckpt.pth"]  # forced (nothing is writing any more)


def test_sync_tree_drops_a_copy_when_the_source_changed_meanwhile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src, dst = tmp_path / "work", tmp_path / "drive"
    f = write(src / "latest_ckpt.pth", b"epoch1")
    real_copy = shutil.copyfile

    def racing_copy(a: Any, b: Any) -> Any:
        out = real_copy(a, b)
        f.write_bytes(b"epoch2 written during the copy")
        return out

    monkeypatch.setattr(shutil, "copyfile", racing_copy)
    res = nbkit.sync_tree(src, dst)
    assert res["unsettled"] == ["latest_ckpt.pth"] and not (dst / "latest_ckpt.pth").exists() and not list(dst.glob("*.part"))


def test_sync_tree_reports_errors_and_keeps_existing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src, dst = tmp_path / "work", tmp_path / "drive"
    write(src / "a.json", b"new")
    write(dst / "a.json", b"old")
    assert nbkit.sync_tree(src, dst, keep_existing=True)["copied"] == [] and (dst / "a.json").read_bytes() == b"old"
    assert nbkit.sync_tree(tmp_path / "missing", dst) == {"copied": [], "unsettled": [], "errors": []}

    def full_drive(a: Any, b: Any) -> Any:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(shutil, "copyfile", full_drive)
    res = nbkit.sync_tree(src, dst)
    assert res["copied"] == [] and "a.json: OSError" in res["errors"][0]


def test_run_signature_is_json_stable() -> None:
    assert nbkit.run_signature(b=1, a=(1, 2)) == {"a": [1, 2], "b": 1}


def test_prepare_persist_fresh_resumed_and_archived(tmp_path: Path) -> None:
    work, persist = tmp_path / "work", tmp_path / "drive" / "yolox_nano"
    sig = nbkit.run_signature(act="silu", epochs=150)
    logs: list[str] = []
    assert nbkit.prepare_persist(persist, work, sig, resume=True, log=logs.append) == "fresh"
    assert json.loads((persist / nbkit.PERSIST_SIGNATURE).read_text(encoding="utf-8")) == sig

    write(persist / "yolox_outputs" / "exp" / "latest_ckpt.pth", b"epoch 42")  # a dropped session left this on Drive
    write(work / "cfg" / "run.yaml", b"fresh config of THIS session")
    write(persist / "cfg" / "run.yaml", b"stale config")
    assert nbkit.prepare_persist(persist, work, sig, resume=True, log=logs.append) == "resumed"
    assert (work / "yolox_outputs" / "exp" / "latest_ckpt.pth").read_bytes() == b"epoch 42"
    assert (work / "cfg" / "run.yaml").read_bytes() == b"fresh config of THIS session"  # restore never clobbers
    assert not (work / nbkit.PERSIST_SIGNATURE).exists() and any("dilanjutkan" in m for m in logs)

    other = nbkit.run_signature(act="relu", epochs=150)  # another experiment must not continue those checkpoints
    assert nbkit.prepare_persist(persist, tmp_path / "work2", other, resume=True, log=logs.append, clock=lambda: NOW) == "archived"
    archived = persist.with_name("yolox_nano.prev-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(NOW)))
    assert (archived / "yolox_outputs" / "exp" / "latest_ckpt.pth").read_bytes() == b"epoch 42"  # moved, never deleted
    assert not (persist / "yolox_outputs").exists() and any("pengaturan berbeda" in m for m in logs)

    write(persist / "yolox_outputs" / "x.pth", b"y")
    assert nbkit.prepare_persist(persist, tmp_path / "work3", other, resume=False, log=logs.append, clock=lambda: NOW + 5) == "archived"
    assert any("RESUME = False" in m for m in logs)


def test_prepare_persist_without_a_signature_is_a_different_experiment(tmp_path: Path) -> None:
    persist = tmp_path / "drive" / "r"
    write(persist / "latest_ckpt.pth", b"unknown origin")
    state = nbkit.prepare_persist(persist, tmp_path / "work", nbkit.run_signature(a=1), resume=True, log=lambda m: None)
    assert state == "archived" and (persist / nbkit.PERSIST_SIGNATURE).is_file()


def test_prepare_persist_fails_loudly_when_the_restore_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    persist, sig = tmp_path / "drive" / "r", nbkit.run_signature(a=1)
    nbkit.prepare_persist(persist, tmp_path / "w", sig, resume=True, log=lambda m: None)
    write(persist / "latest_ckpt.pth", b"ckpt")

    def broken(a: Any, b: Any) -> Any:
        raise OSError("drive offline")

    monkeypatch.setattr(shutil, "copyfile", broken)
    with pytest.raises(RuntimeError, match="could not restore the previous session"):
        nbkit.prepare_persist(persist, tmp_path / "w2", sig, resume=True, log=lambda m: None)


def test_mirror_thread_and_final_sync(tmp_path: Path) -> None:
    work, drive = tmp_path / "work", tmp_path / "drive"
    logs: list[str] = []
    m = nbkit.Mirror(work, drive, every_s=0.05, settle_s=0.0, log=logs.append).start()
    write(work / "yolox_outputs" / "latest_ckpt.pth", b"epoch1")
    deadline = time.time() + 10
    while not (drive / "yolox_outputs" / "latest_ckpt.pth").exists() and time.time() < deadline:
        time.sleep(0.05)
    assert (drive / "yolox_outputs" / "latest_ckpt.pth").read_bytes() == b"epoch1"  # copied by the background thread
    write(work / "download.zip", b"PK")
    assert m.stop()["errors"] == [] and (drive / "download.zip").read_bytes() == b"PK"  # the final pass (or the thread) copied it
    assert m.stop(final=False) == {"copied": [], "unsettled": [], "errors": []}


def test_mirror_warns_once_per_error_and_survives_a_crashing_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = tmp_path / "work"
    logs: list[str] = []
    write(work / "a.json", b"1")
    m = nbkit.Mirror(work, tmp_path / "drive", every_s=0.02, settle_s=0.0, log=logs.append)
    monkeypatch.setattr(shutil, "copyfile", lambda a, b: (_ for _ in ()).throw(OSError(28, "full")))
    m.sync()
    m.sync()
    assert sum("a.json" in x and "PERINGATAN Drive" in x for x in logs) == 1  # a full Drive is reported once, not every pass

    def boom(**kw: Any) -> Any:
        raise ValueError("boom")

    monkeypatch.setattr(m, "sync", boom)
    m.start()
    deadline = time.time() + 10
    while not any("pencerminan gagal" in x for x in logs) and time.time() < deadline:
        time.sleep(0.02)
    m.stop(final=False)
    assert any("pencerminan gagal sekali: ValueError" in x for x in logs)


def test_start_persistence_replaces_the_previous_mirror(tmp_path: Path) -> None:
    sig = nbkit.run_signature(a=1)
    logs: list[str] = []
    state, first = nbkit.start_persistence(tmp_path / "w", tmp_path / "d" / "r", sig, resume=True, every_s=3600, log=logs.append)
    assert state == "fresh" and first._thread is not None and first._thread.is_alive()
    state2, second = nbkit.start_persistence(tmp_path / "w", tmp_path / "d" / "r", sig, resume=True, every_s=3600, log=logs.append)
    assert state2 == "resumed" and second is not first
    assert first._thread is not None and not first._thread.is_alive() and second._thread is not None and second._thread.is_alive()
    second.stop()
    assert any("dicerminkan ke" in x for x in logs)
