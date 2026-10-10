"""License gate decisions + repo-level guards (no AGPL/Ultralytics anywhere in the pipeline)."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest

from license_gate import (
    OWNER_ACCEPTED_RISK,
    LicenseGateError,
    detect_compute_env,
    evaluate_gate,
    load_gate,
    model_basis,
    model_fully_verified,
    weights_basis,
)

DETECTION = Path(__file__).resolve().parents[1]
DATA = DETECTION.parent / "data"


def test_gate_file_loads(gate: dict[str, Any]) -> None:
    assert load_gate()["schema_version"] == 1
    assert gate["datasets"]["ahmed2025"]["license"] == "CC-BY-4.0"
    assert gate["datasets"]["alphadent"]["status"] == "internal_only"
    assert gate["datasets"]["kaggle_oral_diseases"]["status"] == "forbidden"
    assert gate["datasets"]["local_children_id"]["may_upload_to_third_party_compute"] is False
    assert gate["models"]["ultralytics_yolov8"]["status"] == "forbidden_in_product"


def test_load_gate_rejects_garbage(tmp_path: Path) -> None:
    p = tmp_path / "g.yaml"
    p.write_text("a: 1\n", encoding="utf-8")
    with pytest.raises(LicenseGateError):
        load_gate(p)


def test_every_gate_id_documented_in_licenses_md(gate: dict[str, Any]) -> None:
    text = (DATA / "LICENSES.md").read_text(encoding="utf-8")
    for section in ("datasets", "models"):
        for key in gate[section]:
            assert f"`{key}`" in text, f"{key} missing from LICENSES.md"


def test_every_dependency_documented_in_licenses_md() -> None:
    py = tomllib.loads((DETECTION / "pyproject.toml").read_text(encoding="utf-8"))
    deps = list(py["project"]["dependencies"]) + [d for g in py["project"]["optional-dependencies"].values() for d in g]
    deps += list(py["dependency-groups"]["dev"])
    text = (DATA / "LICENSES.md").read_text(encoding="utf-8").lower()
    for d in deps:
        name = re.split(r"[<>=;\[ ]", d, maxsplit=1)[0].lower()
        assert f"`{name}`" in text, f"dependency {name} missing from LICENSES.md"


def test_no_forbidden_packages_in_deps_or_imports(gate: dict[str, Any]) -> None:
    forbidden = gate["forbidden_packages"]
    pyproject = (DETECTION / "pyproject.toml").read_text(encoding="utf-8").lower()
    lock = (DETECTION / "uv.lock").read_text(encoding="utf-8").lower() if (DETECTION / "uv.lock").exists() else ""
    for pkg in forbidden:
        assert f'"{pkg}' not in pyproject
        assert f'name = "{pkg}"' not in lock
    sources = [*DETECTION.glob("*.py"), *DETECTION.glob("trainers/*.py"), *DETECTION.glob("bake_off/*.py"), *(DATA / "scripts").glob("*.py")]
    pattern = re.compile(r"^\s*(import|from)\s+(" + "|".join(forbidden) + r")\b", re.MULTILINE)
    for src in sources:
        assert not pattern.search(src.read_text(encoding="utf-8")), f"forbidden import in {src}"


def test_lock_has_no_copyleft_licensed_base_packages() -> None:
    """Installed base/dev packages: read their metadata license fields (heavy extras are not installed here)."""
    import importlib.metadata as md

    for name in ("numpy", "pillow", "pyyaml", "jsonschema", "defusedxml"):
        meta = md.metadata(name).json
        text = " ".join(str(meta.get(k, "")) for k in ("license", "license_expression", "classifier"))
        assert "GPL" not in text.replace("LGPL", ""), f"{name}: {text}"


def _gate(model_verified: bool = True, ds_status: str = "allowed", third_party: bool = True) -> dict[str, Any]:
    return {
        "models": {
            "m": {"code": {"verified": model_verified}, "weights": {"verified": model_verified}, "status": "candidate"},
            "bad": {"code": {"verified": True}, "weights": {"verified": True}, "status": "forbidden_in_product"},
        },
        "datasets": {"d": {"status": ds_status, "license": "CC-BY-4.0", "may_upload_to_third_party_compute": third_party}},
    }


def test_fully_verified_passes() -> None:
    d = evaluate_gate(_gate(), candidate="m", datasets=["d"], cfg_license_verified=True, allow_unverified=False)
    assert d.allowed and not d.internal_experiment_only and d.reasons == []
    assert d.dataset_licenses[0]["license"] == "CC-BY-4.0"
    assert model_fully_verified(_gate()["models"]["m"])


def test_unverified_needs_flag_and_marks_internal() -> None:
    d = evaluate_gate(_gate(False), candidate="m", datasets=["d"], cfg_license_verified=False, allow_unverified=False)
    assert not d.allowed and "allow-unverified-license" in d.reasons[-1]
    d2 = evaluate_gate(_gate(False), candidate="m", datasets=["d"], cfg_license_verified=False, allow_unverified=True)
    assert d2.allowed and d2.internal_experiment_only
    # gate verified but config not -> still soft refusal
    d3 = evaluate_gate(_gate(True), candidate="m", datasets=["d"], cfg_license_verified=False, allow_unverified=False)
    assert not d3.allowed


@pytest.mark.parametrize(
    ("kwargs", "gate_kwargs", "reason"),
    [
        ({"candidate": "bad"}, {}, "forbidden"),
        ({"cfg_license_verified": True}, {"model_verified": False}, "config says license_verified=true"),
        ({}, {"ds_status": "forbidden"}, "is forbidden"),
        ({}, {"ds_status": "weird"}, "unknown status"),
        ({"compute_env": "kaggle"}, {"third_party": False}, "must not be processed on kaggle"),
        ({"datasets": []}, {}, "no datasets"),
    ],
)
def test_hard_refusals_ignore_flag(kwargs: dict[str, Any], gate_kwargs: dict[str, Any], reason: str) -> None:
    args: dict[str, Any] = {"candidate": "m", "datasets": ["d"], "cfg_license_verified": False, "allow_unverified": True, **kwargs}
    d = evaluate_gate(_gate(**gate_kwargs), **args)
    assert not d.allowed
    assert any(reason in r for r in d.reasons)


@pytest.mark.parametrize("status", ["internal_only", "blocked_unverified"])
def test_soft_dataset_status(status: str) -> None:
    d = evaluate_gate(_gate(ds_status=status), candidate="m", datasets=["d"], cfg_license_verified=True, allow_unverified=True)
    assert d.allowed and d.internal_experiment_only


def test_unknown_ids() -> None:
    with pytest.raises(LicenseGateError, match="candidate"):
        evaluate_gate(_gate(), candidate="zzz", datasets=["d"], cfg_license_verified=False, allow_unverified=True)
    with pytest.raises(LicenseGateError, match="dataset"):
        evaluate_gate(_gate(), candidate="m", datasets=["zzz"], cfg_license_verified=False, allow_unverified=True)


def test_real_gate_alphadent_and_child_data(gate: dict[str, Any]) -> None:
    d = evaluate_gate(gate, candidate="yolox_nano", datasets=["ahmed2025", "local_children_id"], cfg_license_verified=False,
                      allow_unverified=True, compute_env="colab")
    assert not d.allowed and any("local_children_id" in r for r in d.reasons)
    d2 = evaluate_gate(gate, candidate="yolox_nano", datasets=["ahmed2025"], cfg_license_verified=False, allow_unverified=True,
                       compute_env="kaggle")
    assert d2.allowed and d2.internal_experiment_only


@pytest.mark.parametrize(
    ("env", "mods", "expected"),
    [({"KAGGLE_KERNEL_RUN_TYPE": "x"}, [], "kaggle"), ({}, ["google.colab"], "colab"), ({"COLAB_GPU": "1"}, [], "colab"), ({}, [], "local")],
)
def test_detect_compute_env(env: dict[str, str], mods: list[str], expected: str) -> None:
    assert detect_compute_env(env, mods) == expected


@pytest.mark.parametrize(
    ("weights", "expected"),
    [({"verified": True}, "verified"), ({"verified": False}, "unverified"), ({"verified": False, "owner_accepted_risk": True}, OWNER_ACCEPTED_RISK),
     ({"verified": True, "basis": OWNER_ACCEPTED_RISK}, OWNER_ACCEPTED_RISK), ({"license_note": OWNER_ACCEPTED_RISK}, OWNER_ACCEPTED_RISK)],
)
def test_weights_basis(weights: dict[str, Any], expected: str) -> None:
    model = {"code": {"verified": True}, "weights": weights}
    assert weights_basis(model) == expected == model_basis(model)
    assert model_fully_verified(model) is (expected == "verified")
    assert model_basis({"code": {"verified": False}, "weights": weights}) == "unverified"


def test_real_gate_yolox_owner_accepted_risk(gate: dict[str, Any]) -> None:
    """ADR 014 §5: allowed like a verified licence, but recorded distinctly + warned; never 'verified'."""
    assert weights_basis(gate["models"]["yolox_nano"]) == OWNER_ACCEPTED_RISK
    d = evaluate_gate(gate, candidate="yolox_nano", datasets=["ahmed2025"], cfg_license_verified=True, allow_unverified=False,
                      cfg_license_note=OWNER_ACCEPTED_RISK)
    assert d.allowed and not d.internal_experiment_only and d.license_basis == OWNER_ACCEPTED_RISK
    assert d.warnings and "owner_accepted_risk" in d.warnings[0]
    for note in (None, "weird"):  # missing / unknown note -> hard refusal even with the flag
        bad = evaluate_gate(gate, candidate="yolox_nano", datasets=["ahmed2025"], cfg_license_verified=True, allow_unverified=True,
                            cfg_license_note=note)
        assert not bad.allowed
    soft = evaluate_gate(gate, candidate="yolox_nano", datasets=["ahmed2025"], cfg_license_verified=False, allow_unverified=True)
    assert soft.allowed and soft.internal_experiment_only and soft.license_basis == "unverified"


def test_note_without_gate_acceptance_is_refused() -> None:
    g = _gate()
    d = evaluate_gate(g, candidate="m", datasets=["d"], cfg_license_verified=True, allow_unverified=True, cfg_license_note=OWNER_ACCEPTED_RISK)
    assert not d.allowed and any("mismatch" in r for r in d.reasons)
