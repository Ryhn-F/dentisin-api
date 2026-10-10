from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

import train
from trainers import ADAPTERS, load_adapter
from trainers.base import MissingOptionalDependencyError, TrainContext, require

NOW = datetime(2026, 10, 9, 1, 2, 3, tzinfo=UTC)
VERIFIED_IN_WEIGHT_LICENSES = {"mediapipe_modelmaker_mobilenetv2_ssd", "efficientdet_lite0", "rfdetr_nano", "yolox_nano"}
OWNER_ACCEPTED = {"yolox_nano"}  # ADR 014 §5: license_verified true BUT license_note owner_accepted_risk


def test_weight_licenses_doc_matches_configs() -> None:
    text = (train.HERE / "WEIGHT_LICENSES.md").read_text(encoding="utf-8")
    for p in sorted((train.HERE / "configs").glob("*.yaml")):
        cfg, _ = train.load_config(p)
        row = next(ln for ln in text.splitlines() if ln.startswith(f"| `{p.stem}`"))
        assert row.rstrip().endswith(f"| `{str(cfg['license_verified']).lower()}` |"), row


def test_all_repo_configs_validate() -> None:
    cfgs = sorted((train.HERE / "configs").glob("*.yaml"))
    assert {p.stem for p in cfgs} == {"mediapipe_modelmaker_mobilenetv2_ssd", "yolox_nano", "efficientdet_lite0", "rfdetr_nano"}
    datas = set()
    for p in cfgs:
        cfg, _ = train.load_config(p)
        # license_verified=true only where WEIGHT_LICENSES.md records a primary-source verification (2026-10-09)
        assert cfg["license_verified"] is (p.stem in VERIFIED_IN_WEIGHT_LICENSES), p.stem
        assert (cfg.get("license_note") == "owner_accepted_risk") is (p.stem in OWNER_ACCEPTED), p.stem
        assert cfg["export"]["max_size_mb"] <= 15
        datas.add(json.dumps(cfg["data"], sort_keys=True))
    assert len(datas) == 1, "all candidates must use the same data + split"


@pytest.mark.parametrize(
    ("override", "msg"),
    [({"trainer": "ultralytics"}, "trainer"), ({"data__classes": ["caries_primary", "caries_permanent"]}, "canonical order"),
     ({"export__format": "onnx"}, "export/format")],
)
def test_invalid_configs(write_cfg: Callable[..., Path], override: dict[str, Any], msg: str) -> None:
    with pytest.raises(train.ConfigError, match=msg):
        train.load_config(write_cfg(**override))


def test_non_mapping_config(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text("- 1\n", encoding="utf-8")
    with pytest.raises(train.ConfigError, match="mapping"):
        train.load_config(p)


def test_refuses_unverified_license(write_cfg: Callable[..., Path], tmp_path: Path) -> None:
    code, rec = train.run(write_cfg(), runs_dir=tmp_path / "runs", environ={}, now=lambda: NOW)
    assert code == 2 and rec["status"] == "refused_license"
    assert not (tmp_path / "runs").exists()
    assert train.main(["--cfg", str(write_cfg()), "--runs-dir", str(tmp_path / "runs")]) == 2


def test_dry_run_writes_record(write_cfg: Callable[..., Path], tmp_path: Path, data_files: dict[str, Path]) -> None:
    def boom(_: str) -> Any:
        raise AssertionError("dry run must not import trainers")

    code, rec = train.run(write_cfg(), dry_run=True, allow_unverified_license=True, runs_dir=tmp_path / "runs",
                          environ={}, now=lambda: NOW, adapter_loader=boom)
    assert code == 0 and rec["status"] == "dry_run"
    assert rec["license_gate"]["internal_experiment_only"] is True
    assert rec["run_id"].startswith("20261009T010203Z_yolox_nano_")
    saved = json.loads((tmp_path / "runs" / rec["run_id"] / "run.json").read_text(encoding="utf-8"))
    assert saved["seed"] == 20261008
    assert saved["data"]["split_manifest_sha256_match"] is True
    assert saved["data"]["split_sha256"] == train.sha256_file(data_files["splits"])
    assert saved["environment"]["packages"]["numpy"] is not None
    assert saved["environment"]["packages"]["torch"] is None or isinstance(saved["environment"]["packages"]["torch"], str)
    assert "accuracy claim" in saved["claims"]


def test_dry_run_tolerates_missing_data(write_cfg: Callable[..., Path], tmp_path: Path) -> None:
    cfg = write_cfg(data__canonical=str(tmp_path / "nope.jsonl"))
    code, rec = train.run(cfg, dry_run=True, allow_unverified_license=True, runs_dir=tmp_path / "r", environ={}, now=lambda: NOW)
    assert code == 0 and rec["data"]["warnings"]
    with pytest.raises(train.ConfigError, match="not found"):
        train.run(cfg, allow_unverified_license=True, runs_dir=tmp_path / "r", environ={}, now=lambda: NOW)


def test_split_hash_mismatch(write_cfg: Callable[..., Path], tmp_path: Path, data_files: dict[str, Path]) -> None:
    (data_files["splits"].parent / "split_manifest.json").write_text('{"splits_sha256": "deadbeef"}', encoding="utf-8")
    with pytest.raises(train.ConfigError, match="sha256"):
        train.run(write_cfg(), dry_run=True, allow_unverified_license=True, runs_dir=tmp_path / "r", environ={})
    (data_files["splits"].parent / "split_manifest.json").unlink()
    _, rec = train.run(write_cfg(), dry_run=True, allow_unverified_license=True, runs_dir=tmp_path / "r", environ={})
    assert "split_manifest.json missing" in rec["data"]["warnings"][0]


@pytest.mark.parametrize(
    ("behaviour", "code", "status"),
    [("ok", 0, "completed"), ("missing", 3, "failed_missing_dependency"), ("crash", 4, "failed")],
)
def test_dispatch(write_cfg: Callable[..., Path], tmp_path: Path, behaviour: str, code: int, status: str) -> None:
    seen: list[TrainContext] = []

    def fake_train(ctx: TrainContext) -> dict[str, Any]:
        seen.append(ctx)
        if behaviour == "missing":
            raise MissingOptionalDependencyError("install torch")
        if behaviour == "crash":
            raise RuntimeError("nan loss")
        return {"artifacts": []}

    rc, rec = train.run(write_cfg(), allow_unverified_license=True, runs_dir=tmp_path / "r", environ={}, now=lambda: NOW,
                        adapter_loader=lambda name: SimpleNamespace(train=fake_train))
    assert (rc, rec["status"]) == (code, status)
    assert seen[0].seed == 20261008
    saved = json.loads((tmp_path / "r" / rec["run_id"] / "run.json").read_text(encoding="utf-8"))
    assert saved["status"] == status and "finished_at" in saved


def test_child_data_blocked_on_colab(write_cfg: Callable[..., Path], tmp_path: Path) -> None:
    cfg = write_cfg(data__datasets=["local_children_id"])
    code, rec = train.run(cfg, dry_run=True, allow_unverified_license=True, runs_dir=tmp_path / "r", environ={"COLAB_GPU": "1"})
    assert code == 2 and rec["compute_env"] == "colab"


def test_main_dry_run_and_config_error(write_cfg: Callable[..., Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert train.main(["--cfg", str(write_cfg()), "--dry-run", "--allow-unverified-license", "--runs-dir", str(tmp_path / "r")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "dry_run" and out["internal_experiment_only"] is True
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump({"schema_version": 2}), encoding="utf-8")
    assert train.main(["--cfg", str(bad)]) == 2


def test_resolve_and_env() -> None:
    assert train.resolve(Path("/x"), None) is None
    abs_path = Path(__file__).resolve()
    assert train.resolve(Path("/x"), str(abs_path)) == abs_path
    info = train.environment_info(("numpy", "definitely-not-installed-pkg"))
    assert info["packages"]["definitely-not-installed-pkg"] is None


def test_adapters_registry_and_missing_dependency() -> None:
    assert set(ADAPTERS) == {"mediapipe_model_maker", "yolox", "efficientdet_lite", "rfdetr"}
    for name in ADAPTERS:
        assert hasattr(load_adapter(name), "train")
    with pytest.raises(KeyError):
        load_adapter("ultralytics")
    with pytest.raises(MissingOptionalDependencyError, match="pip install foo"):
        require("definitely_not_a_module_xyz", "pip install foo")
    assert require("json", "-").dumps([]) == "[]"


def test_yolox_exp_render(tmp_path: Path, config_dict: dict[str, Any]) -> None:
    from trainers.yolox_adapter import render_exp

    ctx = TrainContext(cfg=config_dict, run_dir=tmp_path, canonical=tmp_path, splits=tmp_path, images_root=None, seed=5)
    text = render_exp(ctx, tmp_path / "data")
    compile(text, "exp.py", "exec")
    assert "self.num_classes = 2" in text and "self.input_size = (416, 416)" in text and "self.seed = 5" in text


def test_dry_run_records_owner_accepted_risk(write_cfg: Callable[..., Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = write_cfg(license_verified=True, license_note="owner_accepted_risk")
    assert train.main(["--cfg", str(cfg), "--dry-run", "--runs-dir", str(tmp_path / "runs")]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["license_basis"] == "owner_accepted_risk" and "WARNING" in captured.err
    rec = json.loads(next((tmp_path / "runs").glob("*/run.json")).read_text(encoding="utf-8"))
    assert rec["license_gate"]["basis"] == "owner_accepted_risk" and rec["license_gate"]["internal_experiment_only"] is False
    assert rec["license_gate"]["warnings"]
