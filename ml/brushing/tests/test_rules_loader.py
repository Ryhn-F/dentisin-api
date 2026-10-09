from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from rules_loader import DEFAULT_RULES_PATH, Rules, load_rules, rules_from_mapping


def _raw() -> dict[str, Any]:
    with DEFAULT_RULES_PATH.open(encoding="utf-8") as fh:
        data: dict[str, Any] = yaml.safe_load(fh)
    return data


def test_default_rules_load(rules: Rules) -> None:
    assert rules.target_duration_ms == 120_000
    assert rules.timezone == "Asia/Jakarta"
    assert [w.name for w in rules.windows] == ["morning", "evening"]
    assert rules.streak_requires == "both"


def test_placeholders_are_reported(rules: Rules) -> None:
    placeholders = rules.placeholder_params()
    assert "min_motion_variance" in placeholders
    assert "windows" in placeholders
    assert "target_duration_s" not in placeholders  # fixed by guideline
    assert placeholders == sorted(placeholders)


def test_overrides_apply_but_keep_status() -> None:
    r = load_rules(overrides={"target_duration_s": 10})
    assert r.target_duration_s == 10
    assert r.statuses["target_duration_s"] == "FIXED_BY_GUIDELINE"


def test_unknown_override_rejected() -> None:
    with pytest.raises(ValueError, match="unknown override"):
        load_rules(overrides={"nope": 1})


def test_load_from_explicit_path(tmp_path: Path) -> None:
    path = tmp_path / "r.yaml"
    path.write_text(yaml.safe_dump(_raw()), encoding="utf-8")
    assert load_rules(path).version == "0.1.0"


def test_non_mapping_file_rejected(tmp_path: Path) -> None:
    path = tmp_path / "r.yaml"
    path.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_rules(path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d.update(schema="other/1"), "unsupported rules schema"),
        (lambda d: d.update(extra=1), "unknown top-level"),
        (lambda d: d.update(params=[]), "'params' mapping"),
        (lambda d: d["params"].pop("window_ms"), "missing"),
        (lambda d: d["params"].update(bogus={"value": 1, "status": "PLACEHOLDER"}), "unknown"),
        (lambda d: d["params"]["window_ms"].pop("status"), "needs 'value' and 'status'"),
        (lambda d: d["params"].update(window_ms=5), "needs 'value' and 'status'"),
        (lambda d: d["params"]["window_ms"].update(status="MAYBE"), "invalid status"),
        (lambda d: d["params"]["window_ms"].update(value="1500"), "expected int"),
        (lambda d: d["params"]["window_ms"].update(value=True), "expected int"),
        (lambda d: d["params"]["min_face_conf"].update(value="x"), "expected number"),
        (lambda d: d["params"]["min_face_conf"].update(value=True), "expected number"),
        (lambda d: d["params"]["timezone"].update(value=5), "expected string"),
        (lambda d: d["params"]["windows"].update(value="x"), "expected a list"),
        (lambda d: d["params"]["window_ms"].update(value=0), "window_ms must be > 0"),
        (lambda d: d["params"]["min_sextant_ms"].update(value=-1), "min_sextant_ms must be >= 0"),
        (lambda d: d["params"]["min_face_conf"].update(value=1.5), "within"),
        (lambda d: d["params"]["vigor_max_speed"].update(value=0.1), "vigor_max_speed"),
        (
            lambda d: [
                d["params"][k].update(value=0) for k in ("w_duration", "w_vigor", "w_coverage")
            ],
            "weights",
        ),
        (lambda d: d["params"]["streak_requires"].update(value="some"), "streak_requires"),
    ],
)
def test_invalid_rules_rejected(mutate: Any, message: str) -> None:
    data = copy.deepcopy(_raw())
    mutate(data)
    with pytest.raises(ValueError, match=message):
        rules_from_mapping(data)


def test_integer_accepted_for_float_field() -> None:
    data = copy.deepcopy(_raw())
    data["params"]["yaw_ref_deg"]["value"] = 30
    assert rules_from_mapping(data).yaw_ref_deg == 30.0
