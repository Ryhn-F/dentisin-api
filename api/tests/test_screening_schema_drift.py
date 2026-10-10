"""Drift guard: the pydantic request models must mirror
``backend/triage/dist/input.schema.json`` exactly. Regenerating the triage dist
(e.g. a new question or class in rules.yaml) without updating
``app/schemas/screening.py`` fails here.

Only two API-only additions are allowed and checked explicitly:
``preferences`` (recommender input, not a triage field) and ``maxItems`` on
``detections`` (transport cap, MAX_DETECTIONS).
"""

from __future__ import annotations

import json
from typing import Any

from app.schemas.screening import MAX_DETECTIONS, AnalyzeRequest, EvaluateRequest
from triage.loader import read_json
from triage.paths import DIST_DIR

IGNORED_KEYS = {"title", "description", "default", "$schema", "$id"}


def _resolve(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict) and "$ref" in node:
        return _resolve(defs[node["$ref"].split("/")[-1]], defs)
    return node


def normalize(node: Any, defs: dict[str, Any]) -> Any:
    """Canonical form: oneOf/anyOf -> sorted anyOf, const -> enum, no type next to an
    enum, no annotations."""
    node = _resolve(node, defs)
    if not isinstance(node, dict):
        return node
    for key in ("anyOf", "oneOf"):
        if key in node:
            variants = [normalize(v, defs) for v in node[key]]
            return {"anyOf": sorted(variants, key=lambda v: json.dumps(v, sort_keys=True))}
    out: dict[str, Any] = {}
    if "const" in node or "enum" in node:
        values = [node["const"]] if "const" in node else node["enum"]
        return {"enum": sorted(values, key=json.dumps)}
    for key, value in node.items():
        if key in IGNORED_KEYS or key == "$defs":
            continue
        if key == "properties":
            out[key] = {k: normalize(v, defs) for k, v in value.items()}
        elif key == "items":
            out[key] = normalize(value, defs)
        elif key == "required":
            out[key] = sorted(value)
        else:
            out[key] = value
    return out


def _model_schema(model: type[EvaluateRequest] | type[AnalyzeRequest]) -> dict[str, Any]:
    schema = model.model_json_schema(by_alias=True, mode="validation")
    return schema


def test_evaluate_request_mirrors_triage_input_schema() -> None:
    dist = read_json(DIST_DIR / "input.schema.json")
    model = _model_schema(EvaluateRequest)
    defs = model.get("$defs", {})
    norm_model = normalize(model, defs)

    # API-only additions, removed explicitly (and nothing else):
    assert "preferences" in norm_model["properties"]
    assert "preferences" not in norm_model.get("required", [])
    del norm_model["properties"]["preferences"]
    assert norm_model["properties"]["detections"].pop("maxItems") == MAX_DETECTIONS

    assert norm_model == normalize(dist, {}), "request model drifted from input.schema.json"


def test_analyze_request_reuses_the_same_triage_fields() -> None:
    dist = normalize(read_json(DIST_DIR / "input.schema.json"), {})
    model = _model_schema(AnalyzeRequest)
    norm = normalize(model, model.get("$defs", {}))
    for field in ("symptoms", "age_months", "pain"):
        assert norm["properties"][field] == dist["properties"][field], field
        assert field in norm["required"]
    assert "detections" not in norm["properties"]  # detections come from the detector only
    assert "photo_status" not in norm["properties"]  # derived from the quality gate
    assert norm["additionalProperties"] is False


def test_normalizer_detects_a_drift() -> None:
    dist = read_json(DIST_DIR / "input.schema.json")
    mutated = json.loads(json.dumps(dist))
    mutated["properties"]["symptoms"]["required"].append("new_question")
    assert normalize(mutated, {}) != normalize(dist, {})
