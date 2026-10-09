"""Pure, deterministic triage engine (Python reference implementation).

Contract (mirrored 1:1 by the TypeScript runtime in ``app/src/domain/triage``):

* ``resolve_params(rules, overrides)`` -> dict of concrete parameter values.
  A ``null`` value falls back to the param's ``if_unset`` (always the more
  cautious choice). Release builds are blocked separately while any value is
  null (see ``triage.release_check``).
* ``evaluate(rules, triage_input, params)`` -> ``{level, reasons, lesions,
  advice_ids, referral_template_id, rules_version}``.

Purity: no I/O, no clock, no randomness, no globals mutated, inputs never
mutated. Every clinical value, symptom list, class list and message lives in
``rules.yaml``; this module only implements the fixed condition vocabulary.

Algorithm:
1. Validate params and input (typed errors, never a silent lower level).
2. Assess each detection: ``rel_area`` = box area / reference area; counted if
   ``confidence >= min_confidence`` of its class; size ``large`` if
   ``rel_area >= X`` else ``small`` (null when not counted).
3. Walk ``rules`` in file order; a rule fires when its ``when`` condition
   holds. ``raise`` rules contribute a level; ``info`` rules never do.
4. Final level = highest-ranked level among fired ``raise`` rules. Because red
   flags are always the top level, this equals first-match in the order
   red flag -> highest score -> lesion count -> default.
5. ``reasons`` = every fired rule (file order). ``advice_ids`` = advice of the
   fired ``raise`` rules at the final level and of fired ``info`` rules (file
   order), then the final level's advice, then ``photo_assessed_advice`` (only
   when the photo was assessed), then ``always_advice``; de-duplicated keeping
   the first occurrence.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import Any

from triage.errors import TriageInputError, TriageParamsError

INPUT_FIELDS = ("detections", "photo_status", "reference_frame", "symptoms", "age_months", "pain")
DETECTION_FIELDS = ("class", "confidence", "box")
BOX_FIELDS = ("x_min", "y_min", "x_max", "y_max")
UNKNOWN = "unknown"
AREA_REFERENCE_FRAME = "frame"

Rules = Mapping[str, Any]
Params = Mapping[str, Any]


# --------------------------------------------------------------------------- helpers
def _is_number(value: object) -> bool:
    """JSON number check with JS semantics (bool is not a number; finite only)."""
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _is_integral(value: object) -> bool:
    """Mirror of JS ``Number.isInteger`` (accepts 3.0)."""
    return _is_number(value) and float(value).is_integer()  # type: ignore[arg-type]


def _in_range(value: Any, low: Any, high: Any) -> bool:
    return bool(low <= value <= high)


def _box_area(box: Mapping[str, Any]) -> float:
    return float((box["x_max"] - box["x_min"]) * (box["y_max"] - box["y_min"]))


def _level_ranks(rules: Rules) -> dict[str, int]:
    return {level["id"]: level["rank"] for level in rules["levels"]}


# --------------------------------------------------------------------------- params
def check_params(rules: Rules, params: Params) -> None:
    """Raise ``TriageParamsError`` unless ``params`` is a complete, valid value set."""
    specs: Mapping[str, Any] = rules["params"]
    for name in params:
        if name not in specs:
            raise TriageParamsError("PARAM_UNKNOWN", f"unknown param '{name}'")
    for name, spec in specs.items():
        if name not in params:
            raise TriageParamsError("PARAM_MISSING", f"param '{name}' has no value")
        value = params[name]
        if spec["type"] in ("number", "integer"):
            valid = (
                _is_integral(value) if spec["type"] == "integer" else _is_number(value)
            ) and _in_range(value, spec["min"], spec["max"])
        else:
            valid = isinstance(value, str) and value in spec["allowed"]
        if not valid:
            raise TriageParamsError("PARAM_INVALID", f"param '{name}' has invalid value {value!r}")


def resolve_params(rules: Rules, overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return concrete values: override -> rules.yaml value -> ``if_unset``.

    ``overrides`` is used only by tests (``tests/fixtures/params_profiles.json``);
    production passes ``None`` so the values come from ``rules.yaml``.
    """
    specs: Mapping[str, Any] = rules["params"]
    chosen: Mapping[str, Any] = {} if overrides is None else overrides
    for name in chosen:
        if name not in specs:
            raise TriageParamsError("PARAM_UNKNOWN", f"unknown param '{name}'")
    resolved: dict[str, Any] = {}
    for name, spec in specs.items():
        value = chosen[name] if name in chosen else spec["value"]
        resolved[name] = spec["if_unset"] if value is None else value
    check_params(rules, resolved)
    return resolved


# --------------------------------------------------------------------------- input
def _check_box(box: object, code: str, label: str) -> Mapping[str, Any]:
    if not isinstance(box, Mapping) or sorted(box) != sorted(BOX_FIELDS):
        raise TriageInputError(code, f"{label} must be an object with keys {BOX_FIELDS}")
    for key in BOX_FIELDS:
        if not _is_number(box[key]) or not _in_range(box[key], 0, 1):
            raise TriageInputError(code, f"{label}.{key} must be a number in [0, 1]")
    if not (box["x_min"] < box["x_max"] and box["y_min"] < box["y_max"]):
        raise TriageInputError(code, f"{label} must have x_min < x_max and y_min < y_max")
    return box


def _check_input(rules: Rules, params: Params, data: object) -> Mapping[str, Any]:
    if not isinstance(data, Mapping):
        raise TriageInputError("INPUT_NOT_OBJECT", "input must be an object")
    for key in data:
        if key not in INPUT_FIELDS:
            raise TriageInputError("UNKNOWN_FIELD", f"unknown input field '{key}'")
    for key in INPUT_FIELDS:
        if key not in data:
            raise TriageInputError("MISSING_FIELD", f"missing input field '{key}'")

    statuses = {status["id"]: status["assessed"] for status in rules["photo_statuses"]}
    photo_status = data["photo_status"]
    if not isinstance(photo_status, str) or photo_status not in statuses:
        raise TriageInputError("PHOTO_STATUS_INVALID", f"invalid photo_status {photo_status!r}")

    symptoms = data["symptoms"]
    if not isinstance(symptoms, Mapping):
        raise TriageInputError("SYMPTOMS_INVALID", "symptoms must be an object")
    question_ids = [question["id"] for question in rules["questions"]]
    for key in symptoms:
        if key not in question_ids:
            raise TriageInputError("UNKNOWN_SYMPTOM", f"unknown symptom '{key}'")
    for key in question_ids:
        if key not in symptoms:
            raise TriageInputError("MISSING_SYMPTOM", f"missing answer for '{key}'")
        answer = symptoms[key]
        if not isinstance(answer, str) or answer not in rules["answers"]:
            raise TriageInputError("INVALID_ANSWER", f"invalid answer {answer!r} for '{key}'")

    age = data["age_months"]
    limits = rules["input_limits"]
    if age is not None and not (
        _is_integral(age) and _in_range(age, limits["age_months_min"], limits["age_months_max"])
    ):
        raise TriageInputError("AGE_INVALID", f"invalid age_months {age!r}")

    pain = data["pain"]
    scale = rules["pain_scale"]
    if pain != UNKNOWN and not (_is_integral(pain) and _in_range(pain, scale["min"], scale["max"])):
        raise TriageInputError("PAIN_INVALID", f"invalid pain {pain!r}")

    detections = data["detections"]
    if not isinstance(detections, list | tuple):
        raise TriageInputError("DETECTIONS_INVALID", "detections must be an array")
    class_ids = [cls["id"] for cls in rules["detection_classes"]]
    for index, det in enumerate(detections):
        if not isinstance(det, Mapping) or sorted(det) != sorted(DETECTION_FIELDS):
            raise TriageInputError(
                "DETECTION_INVALID", f"detections[{index}] must have keys {DETECTION_FIELDS}"
            )
        if not isinstance(det["class"], str) or det["class"] not in class_ids:
            raise TriageInputError("UNKNOWN_CLASS", f"detections[{index}].class {det['class']!r}")
        if not _is_number(det["confidence"]) or not _in_range(det["confidence"], 0, 1):
            raise TriageInputError("CONFIDENCE_INVALID", f"detections[{index}].confidence")
        _check_box(det["box"], "BOX_INVALID", f"detections[{index}].box")
    if detections and not statuses[photo_status]:
        raise TriageInputError(
            "DETECTIONS_WITHOUT_PHOTO", "detections given but photo_status is not assessed"
        )

    frame = data["reference_frame"]
    if frame is not None:
        _check_box(frame, "REFERENCE_FRAME_INVALID", "reference_frame")
    reference = params[rules["lesion_size"]["area_reference_param"]]
    if reference == AREA_REFERENCE_FRAME and detections and frame is None:
        raise TriageInputError(
            "REFERENCE_FRAME_REQUIRED", "lesion_area_reference is 'frame' but no reference_frame"
        )
    return data


# --------------------------------------------------------------------------- lesions
def _assess_lesions(rules: Rules, params: Params, data: Mapping[str, Any]) -> list[dict[str, Any]]:
    size_cfg = rules["lesion_size"]
    large_min = params[size_cfg["large_min_rel_area_param"]]
    use_frame = params[size_cfg["area_reference_param"]] == AREA_REFERENCE_FRAME
    min_conf = {
        cls["id"]: params[cls["min_confidence_param"]] for cls in rules["detection_classes"]
    }
    lesions: list[dict[str, Any]] = []
    for index, det in enumerate(data["detections"]):
        # The frame is only required (and only read) when there is a detection to size.
        reference_area = _box_area(data["reference_frame"]) if use_frame else 1.0
        rel_area = _box_area(det["box"]) / reference_area
        counted = bool(det["confidence"] >= min_conf[det["class"]])
        size = ("large" if rel_area >= large_min else "small") if counted else None
        lesions.append(
            {
                "detection_index": index,
                "class": det["class"],
                "confidence": det["confidence"],
                "rel_area": rel_area,
                "counted": counted,
                "size": size,
            }
        )
    return lesions


# --------------------------------------------------------------------------- conditions
class _Context:
    """Read-only view of the validated input used by condition functions."""

    def __init__(
        self,
        rules: Rules,
        params: Params,
        data: Mapping[str, Any],
        lesions: list[dict[str, Any]],
    ) -> None:
        self.rules = rules
        self.params = params
        self.symptoms: Mapping[str, str] = data["symptoms"]
        self.pain: Any = data["pain"]
        self.age: Any = data["age_months"]
        self.photo_assessed = any(
            status["assessed"]
            for status in rules["photo_statuses"]
            if status["id"] == data["photo_status"]
        )
        self.lesions = lesions
        self.ranks = _level_ranks(rules)
        self.fired_levels: list[str] = []


def _symptom_answer(when: Mapping[str, Any], ctx: _Context) -> bool:
    return ctx.symptoms[when["symptom"]] in when["answers"]


def _any_symptom_answer(when: Mapping[str, Any], ctx: _Context) -> bool:
    return any(
        ctx.symptoms[question["id"]] in when["answers"]
        for question in ctx.rules["questions"]
        if question["group"] == when["group"]
    )


def _pain_score_at_least(when: Mapping[str, Any], ctx: _Context) -> bool:
    return ctx.pain != UNKNOWN and bool(ctx.pain >= ctx.params[when["param"]])


def _pain_score_in_range(when: Mapping[str, Any], ctx: _Context) -> bool:
    return ctx.pain != UNKNOWN and bool(
        ctx.params[when["min_param"]] <= ctx.pain < ctx.params[when["below_param"]]
    )


def _pain_unknown(when: Mapping[str, Any], ctx: _Context) -> bool:
    return bool(ctx.pain == UNKNOWN)


def _lesion_count_at_least(when: Mapping[str, Any], ctx: _Context) -> bool:
    threshold = when["count"] if "count" in when else ctx.params[when["count_param"]]
    count = sum(
        1
        for lesion in ctx.lesions
        if lesion["counted"]
        and lesion["class"] == when["class"]
        and (when["size"] == "any" or lesion["size"] == when["size"])
    )
    return bool(count >= threshold)


def _age_outside_validated_range(when: Mapping[str, Any], ctx: _Context) -> bool:
    if not ctx.photo_assessed:
        return False
    if ctx.age is None:
        return True
    return not _in_range(ctx.age, ctx.params[when["min_param"]], ctx.params[when["max_param"]])


def _photo_not_assessed(when: Mapping[str, Any], ctx: _Context) -> bool:
    return not ctx.photo_assessed


def _no_higher_level_rule_fired(when: Mapping[str, Any], ctx: _Context) -> bool:
    lowest = min(ctx.ranks.values())
    return all(ctx.ranks[level] <= lowest for level in ctx.fired_levels)


CONDITIONS: Mapping[str, Callable[[Mapping[str, Any], _Context], bool]] = {
    "symptom_answer": _symptom_answer,
    "any_symptom_answer": _any_symptom_answer,
    "pain_score_at_least": _pain_score_at_least,
    "pain_score_in_range": _pain_score_in_range,
    "pain_unknown": _pain_unknown,
    "lesion_count_at_least": _lesion_count_at_least,
    "age_outside_validated_range": _age_outside_validated_range,
    "photo_not_assessed": _photo_not_assessed,
    "no_higher_level_rule_fired": _no_higher_level_rule_fired,
}


def rule_level(rule: Mapping[str, Any], params: Params) -> str | None:
    """Level contributed by a rule (``None`` for ``info`` rules)."""
    if rule["effect"] == "info":
        return None
    if "level_param" in rule:
        return str(params[rule["level_param"]])
    return str(rule["level"])


# --------------------------------------------------------------------------- evaluate
def evaluate(rules: Rules, triage_input: object, params: Params) -> dict[str, Any]:
    """Evaluate one screening. Pure function; see module docstring."""
    check_params(rules, params)
    data = _check_input(rules, params, triage_input)
    lesions = _assess_lesions(rules, params, data)
    ctx = _Context(rules, params, data, lesions)

    fired: list[tuple[Mapping[str, Any], str | None]] = []
    for rule in rules["rules"]:
        if CONDITIONS[rule["when"]["type"]](rule["when"], ctx):
            level = rule_level(rule, params)
            fired.append((rule, level))
            if level is not None:
                ctx.fired_levels.append(level)

    # The default rule guarantees at least one raise rule fires (validated in loader).
    final_level = max(ctx.fired_levels, key=lambda level_id: ctx.ranks[level_id])
    level_cfg = next(level for level in rules["levels"] if level["id"] == final_level)

    advice: list[str] = []
    for rule, level in fired:
        if level is None or level == final_level:
            advice.extend(rule["advice"])
    advice.extend(level_cfg["advice"])
    if ctx.photo_assessed:
        advice.extend(rules["photo_assessed_advice"])
    advice.extend(rules["always_advice"])

    return {
        "level": final_level,
        "reasons": [{"id": rule["id"], "text": rule["message"]} for rule, _ in fired],
        "lesions": lesions,
        "advice_ids": list(dict.fromkeys(advice)),
        "referral_template_id": level_cfg["referral_template_id"],
        "rules_version": rules["version"],
    }
