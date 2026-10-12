"""artifacts.py, kaggle/nbkit.py, kaggle/make_ipynb.py, kaggle/mm_train_py310.py helpers, trainer helpers."""

from __future__ import annotations

import hashlib
import json
import py_compile
import zipfile
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import yaml

import artifacts as art
import make_ipynb
import mm_train_py310
import nbkit
from trainers import mediapipe_mm, yolox_adapter
from trainers.base import MissingOptionalDependencyError, TrainContext

DET = Path(__file__).resolve().parents[1]
KAGGLE = DET / "kaggle"


# ------------------------------------------------------------------------------------- artifacts


def test_forbidden_reason(tmp_path: Path, make_image: Callable[..., Path]) -> None:
    assert art.forbidden_reason(make_image(tmp_path / "a.png")) == "forbidden file type .png"
    disguised = tmp_path / "x.json"
    disguised.write_bytes((tmp_path / "a.png").read_bytes())
    assert "magic" in str(art.forbidden_reason(disguised))
    np.save(tmp_path / "c.npy", np.zeros(2))
    assert art.forbidden_reason(tmp_path / "c.npy") is not None
    webp = tmp_path / "w.bin"
    webp.write_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8 ")
    assert art.forbidden_reason(webp) is not None
    ok = tmp_path / "model.tflite"
    ok.write_bytes(b"\x1c\x00\x00\x00TFL3" + b"\x00" * 8)
    assert art.forbidden_reason(ok) is None


def test_collect(tmp_path: Path, make_image: Callable[..., Path]) -> None:
    bundle = tmp_path / "0.1.0-dev.1"
    bundle.mkdir()
    (bundle / "labels.json").write_text("{}", encoding="utf-8")
    rep = tmp_path / "report.json"
    rep.write_text('{"a": 1}', encoding="utf-8")
    out = tmp_path / "dl"
    res = art.collect(out, [bundle, rep], extra={"candidate": "x"})
    assert {f["path"] for f in res["files"]} == {"0.1.0-dev.1/labels.json", "report.json"}
    assert json.loads((out / "RUN_SUMMARY.json").read_text())["contains_images"] is False
    with zipfile.ZipFile(res["archive"]) as zf:
        assert "dl/RUN_SUMMARY.json" in zf.namelist()
    make_image(bundle / "leak.jpg")
    with pytest.raises(art.ArtifactError, match=r"leak\.jpg"):
        art.collect(out, [bundle])
    (bundle / "leak.jpg").unlink()
    with pytest.raises(art.ArtifactError, match="MiB"):
        art.collect(out, [rep], max_file_mb=0.000001)
    with pytest.raises(art.ArtifactError, match="duplicate"):
        art.collect(out, [rep, rep])
    with pytest.raises(art.ArtifactError, match="not found"):
        art.collect(out, [tmp_path / "nope"])


def test_pack_code(tmp_path: Path, make_image: Callable[..., Path]) -> None:
    ml = tmp_path / "ml"
    (ml / "detection" / ".venv").mkdir(parents=True)
    (ml / "detection" / "train.py").write_text("x = 1\n", encoding="utf-8")
    (ml / "detection" / ".venv" / "big.py").write_text("y\n", encoding="utf-8")
    (ml / "data" / "scripts").mkdir(parents=True)
    (ml / "data" / "scripts" / "s.py").write_text("z\n", encoding="utf-8")
    (ml / "data" / "license_gate.yaml").write_text("a: 1\n", encoding="utf-8")
    proc = ml / "data" / "processed" / "ahmed2025"
    (proc / "splits").mkdir(parents=True)
    (proc / "canonical.jsonl").write_text("{}\n", encoding="utf-8")
    make_image(ml / "data" / "raw" / "img.jpg")
    res = art.pack_code(tmp_path / "c.zip", ml_root=ml, with_processed=True)
    with zipfile.ZipFile(tmp_path / "c.zip") as zf:
        names = set(zf.namelist())
    assert names == {"ml/detection/train.py", "ml/data/scripts/s.py", "ml/data/license_gate.yaml",
                     "ml/data/processed/ahmed2025/canonical.jsonl"}
    assert res["files"] == 4 and art.pack_code(tmp_path / "d.zip", ml_root=ml, with_processed=True)["code_sha256"] == res["code_sha256"]
    (ml / "detection" / "sneaky.json").write_bytes((ml / "data" / "raw" / "img.jpg").read_bytes())
    with pytest.raises(art.ArtifactError, match="sneaky"):
        art.pack_code(tmp_path / "e.zip", ml_root=ml)


def test_artifacts_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rep = tmp_path / "r.json"
    rep.write_text("{}", encoding="utf-8")
    assert art.main(["collect", "--out-dir", str(tmp_path / "o"), "--file", str(rep)]) == 0
    assert art.main(["collect", "--out-dir", str(tmp_path / "o"), "--file", str(tmp_path / "missing")]) == 2
    assert art.main(["pack-code", "--out", str(tmp_path / "code.zip")]) == 0
    assert '"code_sha256"' in capsys.readouterr().out


# ----------------------------------------------------------------------------------------- nbkit


def test_env_and_dirs(tmp_path: Path) -> None:
    assert nbkit.compute_env({"KAGGLE_URL_BASE": "x"}, []) == "kaggle"
    assert nbkit.compute_env({}, ["google.colab"]) == "colab"
    assert nbkit.compute_env({}, []) == "local"
    assert nbkit.default_dirs("kaggle")["tmp"] == Path("/tmp/dentisai")
    assert nbkit.default_dirs("colab")["work"].as_posix().startswith("/content/drive")
    assert nbkit.default_dirs("local", tmp_path)["work"] == tmp_path / "working"
    rec = nbkit.env_record({"k": 1})
    assert rec["k"] == 1 and "python" in rec


def test_find_dir_and_merge(tmp_path: Path) -> None:
    (tmp_path / "in" / "ds" / "v1" / "Dataset" / "Images").mkdir(parents=True)
    assert nbkit.find_dir([tmp_path / "missing", tmp_path / "in"], "Dataset/Images") == tmp_path / "in" / "ds" / "v1"
    assert nbkit.find_dir([tmp_path / "in"], "Nope", max_depth=2) is None
    assert nbkit.deep_merge({"a": {"b": 1, "c": 2}, "d": [1]}, {"a": {"b": 5}, "d": [2]}) == {"a": {"b": 5, "c": 2}, "d": [2]}


def test_write_run_config(tmp_path: Path) -> None:
    cfg = nbkit.write_run_config(DET / "configs" / "yolox_nano.yaml", tmp_path / "c.yaml",
                                 {"data": {"images_root": "/x"}, "trainer_options": {"act": "relu"}})
    assert cfg["data"]["images_root"] == "/x" and cfg["trainer_options"]["act"] == "relu"
    assert cfg["license_verified"] is True and cfg["license_note"] == "owner_accepted_risk"  # copied, never overridden
    assert yaml.safe_load((tmp_path / "c.yaml").read_text())["data"]["datasets"] == ["ahmed2025"]
    for bad in ({"license_verified": True}, {"candidate": "x"}, {"data": {"datasets": ["local_children_id"]}}):
        with pytest.raises(ValueError, match="must not override"):
            nbkit.write_run_config(DET / "configs" / "yolox_nano.yaml", tmp_path / "c.yaml", bad)


def test_processed_and_metadata(tmp_path: Path) -> None:
    proc = tmp_path / "p"
    assert nbkit.check_processed(proc) is None
    (proc / "splits").mkdir(parents=True)
    (proc / "canonical.jsonl").write_text("{}\n", encoding="utf-8")
    splits = proc / "splits" / "splits.json"
    splits.write_text(json.dumps({"images": {"b.jpg": "test", "a.jpg": "train"}, "image_groups": {"a.jpg": "g1"}}), encoding="utf-8")
    manifest = proc / "splits" / "split_manifest.json"
    manifest.write_text(json.dumps({"splits_sha256": hashlib.sha256(splits.read_bytes()).hexdigest(), "group_quality": "heuristic_session"}))
    info = nbkit.check_processed(proc)
    assert info is not None and info["group_quality"] == "heuristic_session"
    manifest.write_text(json.dumps({"splits_sha256": "0" * 64}))
    with pytest.raises(ValueError, match="sha256"):
        nbkit.check_processed(proc)
    assert nbkit.metadata_from_splits(splits, tmp_path / "m.csv") == 2
    assert (tmp_path / "m.csv").read_text().splitlines() == ["image,patient_id,source", "a.jpg,g1,ahmed2025", "b.jpg,,ahmed2025"]


def test_class_map_and_outputs(tmp_path: Path) -> None:
    cm = tmp_path / "cm.yaml"
    cm.write_text("status: PLACEHOLDER\nmap:\n  caries: null\n", encoding="utf-8")
    assert len(nbkit.class_map_problems(cm)) == 2
    cm.write_text("status: decided\nmap:\n  D: caries_permanent\n  d: caries_primary\n", encoding="utf-8")
    assert nbkit.class_map_problems(cm) == []
    for n in ("m_float32.tflite", "m_float16.tflite", "m_integer_quant.tflite", "m_full_integer_quant.tflite",
              "m_integer_quant_with_int16_act.tflite"):
        (tmp_path / n).write_bytes(b"x")
    out = nbkit.pick_onnx2tf_outputs(tmp_path)
    assert {k: v.name if v else None for k, v in out.items()} == {
        "float32": "m_float32.tflite", "int8_float_io": "m_integer_quant.tflite", "int8_full": "m_full_integer_quant.tflite"}
    exp = tmp_path / "exports"
    assert nbkit.next_dev_version(exp) == "0.1.0-dev.1"
    (exp / "0.1.0-dev.7").mkdir(parents=True)
    (exp / "0.1.0-dev.x").mkdir()
    assert nbkit.next_dev_version(exp) == "0.1.0-dev.8"


# ----------------------------------------------------------------------------------- notebooks


def test_notebooks_compile_and_ipynb_in_sync() -> None:
    for name in make_ipynb.SOURCES:
        py_compile.compile(str(KAGGLE / name), doraise=True)
    assert make_ipynb.main(["--check"]) == 0, "run: uv run python kaggle/make_ipynb.py"


def test_make_ipynb_cells(tmp_path: Path) -> None:
    text = "# %% [markdown]\n# # Title\n# body\n#\n\n# %% 1. Step\nx = 1\n\n\n# %%\ny = 2\n"
    nb = make_ipynb.to_notebook(text)
    kinds = [c["cell_type"] for c in nb["cells"]]
    assert kinds == ["markdown", "code", "code"]
    assert nb["cells"][0]["source"] == ["# Title\n", "body\n", ""]
    assert nb["cells"][1]["source"] == ["# 1. Step\n", "x = 1"] and nb["cells"][2]["source"] == ["y = 2"]
    for name in make_ipynb.SOURCES:
        (tmp_path / name).write_text(text, encoding="utf-8")
    assert make_ipynb.main(["--dir", str(tmp_path), "--check"]) == 1
    assert make_ipynb.main(["--dir", str(tmp_path)]) == 0 and make_ipynb.main(["--dir", str(tmp_path), "--check"]) == 0


def test_notebooks_respect_rules() -> None:
    for name in make_ipynb.SOURCES:
        text = (KAGGLE / name).read_text(encoding="utf-8")
        code = text.split("# %% 0.", 1)[1]  # skip the markdown header
        assert "ultralytics" not in text.lower() and "local_children_id" not in text
        assert "license_gate" not in code  # the gate is never read/patched by a notebook (train.py/export.py decide)
        assert '"artifacts.py", "collect"' in code  # downloads always go through the image-refusing collector
        npy_lines = [ln for ln in code.splitlines() if ".npy" in ln and not ln.lstrip().startswith("#")]
        assert npy_lines and all("TMP" in ln for ln in npy_lines)  # derived tensors stay in non-exported scratch


# ------------------------------------------------------------------------------------- trainers


def ctx_for(cfg: dict[str, Any], tmp_path: Path, **opts: Any) -> TrainContext:
    c = json.loads(json.dumps(cfg))
    c.setdefault("trainer_options", {}).update(opts)
    return TrainContext(cfg=c, run_dir=tmp_path, canonical=tmp_path, splits=tmp_path, images_root=None, seed=3)


def test_yolox_helpers(tmp_path: Path, config_dict: dict[str, Any]) -> None:
    ctx = ctx_for(config_dict, tmp_path, act="relu", output_dir=str(tmp_path / "o"))
    text = yolox_adapter.render_exp(ctx, tmp_path / "d")
    compile(text, "exp.py", "exec")
    assert 'self.act = "relu"' in text and "act=self.act" in text and "self.save_history_ckpt = False" in text
    with pytest.raises(ValueError, match="act"):
        yolox_adapter.options(ctx_for(config_dict, tmp_path, act="gelu"))
    opts = yolox_adapter.options(ctx)
    cmd = yolox_adapter.build_command(Path("e.py"), ctx, opts, resume=None, python="py")
    assert cmd[:2] == ["py", "tools/train.py"] and "--fp16" in cmd and cmd[-2:] == ["-c", "yolox_nano.pth"]
    cmd = yolox_adapter.build_command(Path("e.py"), ctx, {**opts, "fp16": False}, resume=Path("l.pth"), python="py")
    assert cmd[-3:] == ["--resume", "-c", "l.pth"] and "--fp16" not in cmd
    assert yolox_adapter.expected_rows((416, 416)) == 3549
    assert len(yolox_adapter.YOLOX_COMMIT) == 40


def test_find_resume_checkpoint(tmp_path: Path) -> None:
    import os

    assert yolox_adapter.find_resume_checkpoint([tmp_path]) is None
    old = tmp_path / "a" / "x" / "latest_ckpt.pth"
    new = tmp_path / "b" / "latest_ckpt.pth"
    for i, p in enumerate((old, new)):
        p.parent.mkdir(parents=True)
        p.write_bytes(b"c")
        os.utime(p, (1000 + i, 1000 + i))
    assert yolox_adapter.find_resume_checkpoint([tmp_path / "a", tmp_path / "b"]) == new
    assert yolox_adapter.find_resume_checkpoint([old]) == old
    roots = yolox_adapter.resume_candidates({"output_dir": str(tmp_path / "o"), "resume_from": str(tmp_path / "b")})
    assert roots == [tmp_path / "o", tmp_path / "b"]
    globbed = yolox_adapter.resume_candidates({"output_dir": "o", "resume_from": [str(tmp_path / "*").replace("\\", "/")]})
    assert len(globbed) >= 2


def test_mediapipe_helpers(tmp_path: Path) -> None:
    cfg = yaml.safe_load((DET / "configs" / "mediapipe_modelmaker_mobilenetv2_ssd.yaml").read_text(encoding="utf-8"))
    ctx = ctx_for(cfg, tmp_path)
    opts = mediapipe_mm.options(ctx)
    assert opts["max_side"] == 640 and opts["cache_dir"].endswith("mm_cache")
    with pytest.raises(MissingOptionalDependencyError, match=r"Python 3\.10"):
        mediapipe_mm.build_command(ctx, opts, tmp_path / "l", tmp_path / "e")
    cmd = mediapipe_mm.build_command(ctx, {**opts, "python": "/venv/bin/python"}, tmp_path / "l", tmp_path / "e")
    assert cmd[0] == "/venv/bin/python" and cmd[1].endswith("mm_train_py310.py")
    assert cmd[cmd.index("--variant") + 1] == "MOBILENET_V2" and cmd[cmd.index("--qat-epochs") + 1] == "10"
    assert mediapipe_mm.VERIFIED_VARIANTS == ("MOBILENET_V2",)


def test_mediapipe_worker_env_is_headless_and_clean() -> None:
    """Kaggle run 2026-10-12: the kernel's inline matplotlib backend and PYTHONPATH must not reach the 3.10 venv."""
    env = mediapipe_mm.child_environment(
        {"MPLBACKEND": "module://matplotlib_inline.backend_inline", "PYTHONPATH": "/k", "PYTHONHOME": "/h", "PATH": "/bin"}
    )
    assert env == {"MPLBACKEND": "Agg", "PATH": "/bin"}


def test_mm_anchor_extraction_matches_model_maker_formula() -> None:
    class T:
        def __init__(self, a: list[list[float]]) -> None:
            self.a = np.asarray(a)

        def numpy(self) -> Any:
            return self.a

    model = SimpleNamespace(_model_spec=SimpleNamespace(input_image_shape=[256, 256, 3]),
                            _preprocessor=SimpleNamespace(anchor_boxes={"3": T([[0, 0, 32, 64]]), "4": T([[128, 64, 256, 128]])}))
    assert mm_train_py310.anchors_from_model(model) == [[0.0625, 0.125, 0.125, 0.25], [0.75, 0.375, 0.5, 0.25]]
