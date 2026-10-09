"""Loads and validates ``brushing_rules.yaml``. The code holds NO threshold defaults on purpose."""

from __future__ import annotations

import dataclasses
import typing
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from checkin import CheckinWindow, parse_windows

DEFAULT_RULES_PATH = Path(__file__).with_name("brushing_rules.yaml")
VALID_STATUSES = frozenset({"PLACEHOLDER", "FIXED_BY_GUIDELINE", "VALIDATED"})
SCHEMA_ID = "brushing_rules/1"


@dataclass(frozen=True)
class Rules:
    """Typed view of the rules file. No defaults: a missing key is an error, not a silent guess."""

    # presence
    min_face_conf: float
    min_hand_conf: float
    near_mouth_max_rel: float
    hand_dropout_grace_ms: int
    # motion / anti-cheat
    window_ms: int
    min_window_samples: int
    min_motion_variance: float
    min_direction_reversals: int
    reversal_min_amplitude: float
    max_mean_speed: float
    # timer
    target_duration_s: int
    max_frame_gap_ms: int
    # coverage
    min_sextant_ms: int
    hand_x_weight: float
    head_yaw_weight: float
    yaw_ref_deg: float
    h_side_threshold: float
    hand_y_weight: float
    head_pitch_weight: float
    pitch_ref_deg: float
    v_split: float
    # enthusiasm
    w_duration: float
    w_vigor: float
    w_coverage: float
    vigor_min_speed: float
    vigor_max_speed: float
    # check-in
    timezone: str
    windows: tuple[CheckinWindow, ...]
    streak_requires: str
    # provenance
    version: str
    statuses: Mapping[str, str]

    @property
    def target_duration_ms(self) -> int:
        return self.target_duration_s * 1000

    def placeholder_params(self) -> list[str]:
        """Names of parameters still PLACEHOLDER (a release gate may refuse to ship if any)."""
        return sorted(k for k, v in self.statuses.items() if v == "PLACEHOLDER")


_META = {"version", "status", "schema", "reviewed_by", "reviewed_at"}


def _coerce(name: str, kind: Any, value: Any) -> Any:
    if kind is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name}: expected int, got {value!r}")
        return value
    if kind is float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"{name}: expected number, got {value!r}")
        return float(value)
    if kind is str:
        if not isinstance(value, str):
            raise ValueError(f"{name}: expected string, got {value!r}")
        return value
    raise TypeError(f"unsupported field type for {name}: {kind!r}")  # pragma: no cover


def _validate(rules: Rules) -> None:
    positive = (
        "window_ms",
        "min_window_samples",
        "target_duration_s",
        "max_frame_gap_ms",
        "yaw_ref_deg",
        "pitch_ref_deg",
        "near_mouth_max_rel",
        "max_mean_speed",
    )
    for name in positive:
        if getattr(rules, name) <= 0:
            raise ValueError(f"{name} must be > 0")
    non_negative = (
        "hand_dropout_grace_ms",
        "min_sextant_ms",
        "min_motion_variance",
        "min_direction_reversals",
        "reversal_min_amplitude",
        "h_side_threshold",
        "w_duration",
        "w_vigor",
        "w_coverage",
        "vigor_min_speed",
    )
    for name in non_negative:
        if getattr(rules, name) < 0:
            raise ValueError(f"{name} must be >= 0")
    for name in ("min_face_conf", "min_hand_conf"):
        if not 0.0 <= getattr(rules, name) <= 1.0:
            raise ValueError(f"{name} must be within [0, 1]")
    if rules.vigor_max_speed <= rules.vigor_min_speed:
        raise ValueError("vigor_max_speed must be > vigor_min_speed")
    if rules.w_duration + rules.w_vigor + rules.w_coverage <= 0:
        raise ValueError("enthusiasm weights must not all be zero")
    if rules.streak_requires not in ("both", "any"):
        raise ValueError("streak_requires must be 'both' or 'any'")


def rules_from_mapping(
    data: Mapping[str, Any], overrides: Mapping[str, Any] | None = None
) -> Rules:
    """Build Rules from the parsed YAML mapping, optionally overriding individual values.

    ``overrides`` is meant for tests/parity cases (e.g. a short ``target_duration_s``); an override
    never changes the recorded status.
    """
    if data.get("schema") != SCHEMA_ID:
        raise ValueError(f"unsupported rules schema: {data.get('schema')!r}")
    unknown_meta = set(data) - _META - {"params"}
    if unknown_meta:
        raise ValueError(f"unknown top-level keys: {sorted(unknown_meta)}")
    params = data.get("params")
    if not isinstance(params, Mapping):
        raise ValueError("'params' mapping is missing")

    hints = typing.get_type_hints(Rules)
    param_fields = {
        f.name: hints[f.name]
        for f in dataclasses.fields(Rules)
        if f.name not in ("version", "statuses")
    }
    missing = set(param_fields) - set(params)
    extra = set(params) - set(param_fields)
    if missing or extra:
        raise ValueError(f"params mismatch: missing={sorted(missing)} unknown={sorted(extra)}")
    overrides = dict(overrides or {})
    bad_override = set(overrides) - set(param_fields)
    if bad_override:
        raise ValueError(f"unknown override keys: {sorted(bad_override)}")

    values: dict[str, Any] = {}
    statuses: dict[str, str] = {}
    for name, kind in param_fields.items():
        entry = params[name]
        if not isinstance(entry, Mapping) or "value" not in entry or "status" not in entry:
            raise ValueError(f"{name}: needs 'value' and 'status'")
        status = entry["status"]
        if status not in VALID_STATUSES:
            raise ValueError(f"{name}: invalid status {status!r}")
        statuses[name] = status
        raw = overrides.get(name, entry["value"])
        if name == "windows":
            if not isinstance(raw, list):
                raise ValueError("windows: expected a list")
            values[name] = parse_windows(raw)
        else:
            values[name] = _coerce(name, kind, raw)

    rules = Rules(version=str(data["version"]), statuses=statuses, **values)
    _validate(rules)
    return rules


def load_rules(path: Path | None = None, overrides: Mapping[str, Any] | None = None) -> Rules:
    """Load rules from YAML (default: ``brushing_rules.yaml`` next to this module)."""
    with (path or DEFAULT_RULES_PATH).open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, Mapping):
        raise ValueError("rules file must contain a mapping")
    return rules_from_mapping(data, overrides)
