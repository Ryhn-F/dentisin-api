"""License gate shared by train.py and export.py (ADR 001/008, backend/CLAUDE.md §3.4).

Pure decision logic: :func:`evaluate_gate` takes the parsed gate YAML and a candidate/dataset
selection and returns a :class:`GateDecision`. No I/O except :func:`load_gate`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_GATE_PATH = Path(__file__).resolve().parent.parent / "data" / "license_gate.yaml"
THIRD_PARTY_COMPUTE = ("kaggle", "colab")


class LicenseGateError(RuntimeError):
    pass


@dataclass
class GateDecision:
    allowed: bool
    internal_experiment_only: bool
    reasons: list[str] = field(default_factory=list)
    model_license: dict[str, Any] = field(default_factory=dict)
    dataset_licenses: list[dict[str, Any]] = field(default_factory=list)


def load_gate(path: Path = DEFAULT_GATE_PATH) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "datasets" not in data or "models" not in data:
        raise LicenseGateError(f"{path}: not a license gate file")
    return data


def model_fully_verified(model: Mapping[str, Any]) -> bool:
    return bool(model.get("code", {}).get("verified")) and bool(model.get("weights", {}).get("verified"))


def evaluate_gate(
    gate: Mapping[str, Any],
    *,
    candidate: str,
    datasets: Sequence[str],
    cfg_license_verified: bool,
    allow_unverified: bool,
    compute_env: str = "local",
) -> GateDecision:
    """Decide whether a run/export may proceed.

    Hard refusals (no flag can override): forbidden model, forbidden dataset, unknown ids,
    child data on third-party compute, config claiming verification the gate does not support.
    Soft refusals (overridable with ``allow_unverified`` -> internal_experiment_only): unverified
    code/weights license, datasets that are blocked_unverified or internal_only.
    """
    hard: list[str] = []
    soft: list[str] = []
    models = gate.get("models", {})
    if candidate not in models:
        raise LicenseGateError(f"candidate {candidate!r} not in license gate (add it to ml/data/license_gate.yaml + LICENSES.md)")
    model = models[candidate]
    if model.get("status") == "forbidden_in_product":
        hard.append(f"model {candidate!r} is forbidden (ADR 001/008)")
    gate_verified = model_fully_verified(model)
    if cfg_license_verified and not gate_verified:
        hard.append(f"config says license_verified=true but the gate has unverified code/weights for {candidate!r}")
    if not gate_verified or not cfg_license_verified:
        soft.append(f"code/weights license of {candidate!r} not verified")

    ds_licenses: list[dict[str, Any]] = []
    all_ds = gate.get("datasets", {})
    if not datasets:
        hard.append("no datasets declared in config")
    for ds_id in datasets:
        if ds_id not in all_ds:
            raise LicenseGateError(f"dataset {ds_id!r} not in license gate")
        ds = all_ds[ds_id]
        ds_licenses.append({"id": ds_id, **{k: ds.get(k) for k in ("name", "license", "license_url", "doi", "url", "attribution_en", "attribution_id")}})
        status = ds.get("status")
        if status == "forbidden":
            hard.append(f"dataset {ds_id!r} is forbidden")
        elif status == "internal_only":
            soft.append(f"dataset {ds_id!r} is internal_only (never for a shipped model)")
        elif status == "blocked_unverified":
            soft.append(f"dataset {ds_id!r} license/permission unverified")
        elif status != "allowed":
            hard.append(f"dataset {ds_id!r} has unknown status {status!r}")
        if compute_env in THIRD_PARTY_COMPUTE and not ds.get("may_upload_to_third_party_compute", False):
            hard.append(f"dataset {ds_id!r} must not be processed on {compute_env} (child data stays on team PC)")

    reasons = hard + soft
    if hard:
        return GateDecision(False, False, reasons, dict(model), ds_licenses)
    if soft and not allow_unverified:
        return GateDecision(False, False, [*reasons, "pass --allow-unverified-license for an internal experiment"], dict(model), ds_licenses)
    return GateDecision(True, bool(soft), reasons, dict(model), ds_licenses)


def detect_compute_env(environ: Mapping[str, str], modules: Sequence[str]) -> str:
    """Best-effort detection of hosted notebooks (used only to block child data there)."""
    if any(k.startswith("KAGGLE_") for k in environ):
        return "kaggle"
    if "google.colab" in modules or "COLAB_RELEASE_TAG" in environ or "COLAB_GPU" in environ:
        return "colab"
    return "local"
