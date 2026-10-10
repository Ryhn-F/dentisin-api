"""Thin training dispatcher for the detector bake-off.

    python train.py --cfg configs/yolox_nano.yaml [--dry-run] [--allow-unverified-license] [--mlflow]

1. validates the config (config.schema.json + canonical class list);
2. runs the license gate (ml/data/license_gate.yaml): refuses if cfg.license_verified is false
   unless --allow-unverified-license, in which case the run is marked internal_experiment_only;
3. hashes the data (canonical JSONL + splits.json) and checks the split hash against
   split_manifest.json, records seed + environment;
4. writes ml/runs/<run_id>/run.json (JSON only, never images);
5. lazily imports the trainer adapter; a missing optional dependency fails with an install hint.

``--dry-run`` stops after step 4 (status "dry_run"); no trainer is imported. This file never
reports metrics it did not receive from a trainer.

Exit codes: 0 ok/dry-run, 2 config or license refusal, 3 missing optional dependency, 4 trainer error.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as md
import json
import platform
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from canonical import CLASS_NAMES
from license_gate import DEFAULT_GATE_PATH, GateDecision, detect_compute_env, evaluate_gate, load_gate
from trainers import load_adapter
from trainers.base import MissingOptionalDependencyError, TrainContext

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "config.schema.json"
DEFAULT_RUNS_DIR = HERE.parent / "runs"
ENV_PACKAGES = ("numpy", "pillow", "pyyaml", "jsonschema", "torch", "torchvision", "tensorflow",
                "mediapipe-model-maker", "yolox", "rfdetr", "onnx", "onnx2tf", "ai-edge-litert")


class ConfigError(ValueError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_config(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    cfg = yaml.safe_load(raw)
    if not isinstance(cfg, dict):
        raise ConfigError(f"{path}: config must be a mapping")
    validate_config(cfg)
    return cfg, raw


def validate_config(cfg: Mapping[str, Any]) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        jsonschema.validate(cfg, schema)
    except jsonschema.ValidationError as exc:
        raise ConfigError(f"config invalid at {'/'.join(map(str, exc.absolute_path)) or '<root>'}: {exc.message}") from exc
    if tuple(cfg["data"]["classes"]) != CLASS_NAMES:
        raise ConfigError(f"data.classes must be exactly {list(CLASS_NAMES)} (canonical order)")


def resolve(base: Path, p: str | None) -> Path | None:
    if p is None:
        return None
    path = Path(p)
    return path if path.is_absolute() else (base / path).resolve()


def environment_info(packages: tuple[str, ...] = ENV_PACKAGES) -> dict[str, Any]:
    versions: dict[str, str | None] = {}
    for name in packages:
        try:
            versions[name] = md.version(name)
        except md.PackageNotFoundError:
            versions[name] = None
    return {
        "python": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": versions,
    }


def data_fingerprint(canonical: Path | None, splits: Path | None, *, require_files: bool) -> dict[str, Any]:
    out: dict[str, Any] = {"canonical_sha256": None, "split_sha256": None, "split_manifest_sha256_match": None, "warnings": []}
    for key, path in (("canonical_sha256", canonical), ("split_sha256", splits)):
        if path is not None and path.is_file():
            out[key] = sha256_file(path)
        elif require_files:
            raise ConfigError(f"data file not found: {path}")
        else:
            out["warnings"].append(f"data file not found (dry run): {path}")
    if splits is not None and out["split_sha256"] is not None:
        manifest = splits.parent / "split_manifest.json"
        if manifest.is_file():
            expected = json.loads(manifest.read_text(encoding="utf-8")).get("splits_sha256")
            out["split_manifest_sha256_match"] = expected == out["split_sha256"]
            if not out["split_manifest_sha256_match"]:
                raise ConfigError(f"splits.json sha256 {out['split_sha256']} != split_manifest.json {expected}")
            out["split_manifest"] = json.loads(manifest.read_text(encoding="utf-8"))
        else:
            out["warnings"].append("split_manifest.json missing next to splits.json")
    return out


def make_run_id(now: datetime, candidate: str, cfg_sha: str) -> str:
    return f"{now.strftime('%Y%m%dT%H%M%SZ')}_{candidate}_{cfg_sha[:8]}"


def write_record(run_dir: Path, record: Mapping[str, Any]) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "run.json"
    path.write_text(json.dumps(record, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return path


def log_mlflow(record: Mapping[str, Any], runs_dir: Path) -> str:  # pragma: no cover - optional dep
    try:
        import mlflow
    except ImportError as exc:
        raise MissingOptionalDependencyError("mlflow not installed: uv sync --extra tracking") from exc
    mlflow.set_tracking_uri((runs_dir / "mlruns").resolve().as_uri())  # local files only, no Azure ML
    mlflow.set_experiment("dentisai-detector-bakeoff")
    with mlflow.start_run(run_name=str(record["run_id"])) as run:
        mlflow.log_params({"candidate": record["candidate"], "seed": record["seed"],
                           "split_sha256": record["data"]["split_sha256"],
                           "internal_experiment_only": record["license_gate"]["internal_experiment_only"]})
        mlflow.log_dict(dict(record), "run.json")
        return str(run.info.run_id)


def run(
    cfg_path: Path,
    *,
    dry_run: bool = False,
    allow_unverified_license: bool = False,
    runs_dir: Path = DEFAULT_RUNS_DIR,
    gate_path: Path = DEFAULT_GATE_PATH,
    use_mlflow: bool = False,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    environ: Mapping[str, str] | None = None,
    adapter_loader: Callable[[str], Any] = load_adapter,
) -> tuple[int, dict[str, Any]]:
    import os

    cfg, raw = load_config(cfg_path)
    base = HERE  # relative paths in configs are relative to ml/detection
    compute_env = detect_compute_env(environ if environ is not None else dict(os.environ), list(sys.modules))
    decision: GateDecision = evaluate_gate(
        load_gate(gate_path),
        candidate=cfg["candidate"],
        datasets=cfg["data"]["datasets"],
        cfg_license_verified=cfg["license_verified"],
        allow_unverified=allow_unverified_license,
        compute_env=compute_env,
        cfg_license_note=cfg.get("license_note"),
    )
    canonical = resolve(base, cfg["data"]["canonical"])
    splits = resolve(base, cfg["data"]["splits"])
    images_root = resolve(base, cfg["data"].get("images_root"))
    cfg_sha = sha256_bytes(raw)
    started = now()
    record: dict[str, Any] = {
        "run_id": make_run_id(started, cfg["candidate"], cfg_sha),
        "created_at": started.isoformat(),
        "candidate": cfg["candidate"],
        "trainer": cfg["trainer"],
        "config_path": cfg_path.as_posix(),
        "config_sha256": cfg_sha,
        "config": cfg,
        "seed": cfg["train"]["seed"],
        "compute_env": compute_env,
        "environment": environment_info(),
        "license_gate": {
            "allowed": decision.allowed,
            "internal_experiment_only": decision.internal_experiment_only,
            "basis": decision.license_basis,   # verified | owner_accepted_risk | unverified (never conflate)
            "warnings": decision.warnings,
            "reasons": decision.reasons,
            "model": decision.model_license,
            "datasets": decision.dataset_licenses,
        },
        "status": "refused_license" if not decision.allowed else ("dry_run" if dry_run else "started"),
        "results": None,
        "claims": "none -- a run record is not an accuracy claim; see eval.py report",
    }
    if not decision.allowed:
        return 2, record
    record["data"] = data_fingerprint(canonical, splits, require_files=not dry_run)
    run_dir = runs_dir / record["run_id"]
    write_record(run_dir, record)
    if use_mlflow:
        record["mlflow_run_id"] = log_mlflow(record, runs_dir)
        write_record(run_dir, record)
    if dry_run:
        return 0, record
    assert canonical is not None
    assert splits is not None
    ctx = TrainContext(cfg=cfg, run_dir=run_dir, canonical=canonical, splits=splits,
                       images_root=images_root, seed=int(cfg["train"]["seed"]))
    try:
        adapter = adapter_loader(cfg["trainer"])
        record["results"] = adapter.train(ctx)
        record["status"] = "completed"
        code = 0
    except MissingOptionalDependencyError as exc:
        record["status"] = "failed_missing_dependency"
        record["error"] = str(exc)
        code = 3
    except Exception as exc:
        record["status"] = "failed"
        record["error"] = f"{type(exc).__name__}: {exc}"
        code = 4
    record["finished_at"] = now().isoformat()
    write_record(run_dir, record)
    return code, record


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cfg", type=Path, required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--allow-unverified-license", action="store_true",
                   help="run anyway; the run is marked internal_experiment_only and must not ship")
    p.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    p.add_argument("--gate", type=Path, default=DEFAULT_GATE_PATH)
    p.add_argument("--mlflow", action="store_true", help="also log to a LOCAL MLflow file store (ml/runs/mlruns)")
    args = p.parse_args(argv)
    try:
        code, record = run(args.cfg, dry_run=args.dry_run, allow_unverified_license=args.allow_unverified_license,
                           runs_dir=args.runs_dir, gate_path=args.gate, use_mlflow=args.mlflow)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    summary = {k: record.get(k) for k in ("run_id", "status", "error")}
    summary["license_gate"] = record["license_gate"]["reasons"]
    summary["internal_experiment_only"] = record["license_gate"]["internal_experiment_only"]
    summary["license_basis"] = record["license_gate"]["basis"]
    for w in record["license_gate"]["warnings"]:
        print(f"WARNING: {w}", file=sys.stderr)
    print(json.dumps(summary, indent=1))
    return code


if __name__ == "__main__":
    sys.exit(main())
