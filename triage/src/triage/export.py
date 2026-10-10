"""Export ``rules.yaml`` to JSON artefacts the TypeScript app consumes.

Usage::

    uv run python -m triage.export          # (re)write dist/
    uv run python -m triage.export --check  # exit 1 if dist/ is stale

Outputs (all UTF-8, LF line endings, byte-for-byte deterministic):

* ``dist/rules.json``          same data as rules.yaml (validated)
* ``dist/input.schema.json``   JSON Schema for the engine input (derived from rules)
* ``dist/output.schema.json``  JSON Schema for the engine output (derived from rules)
* ``dist/SHA256SUMS``          ``<sha256>  <file>`` per artefact (``sha256sum -c`` format)
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from triage.loader import load_rules
from triage.paths import DIST_DIR, RULES_PATH

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def to_json_bytes(data: object) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def build_input_schema(rules: Mapping[str, Any]) -> dict[str, Any]:
    limits = rules["input_limits"]
    scale = rules["pain_scale"]
    return {
        "$schema": SCHEMA_DIALECT,
        "$id": "https://dentisai.local/triage/input.schema.json",
        "title": f"Triage engine input (rules {rules['version']})",
        "description": "Generated from rules.yaml by triage.export. Detections carry only "
        "class + confidence: box geometry is NOT an engine input (ADR 014; strip boxes used for "
        "the UI overlay before calling the engine). Cross-field rule enforced by the engine: "
        "detections require an assessed photo.",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "detections",
            "photo_status",
            "symptoms",
            "age_months",
            "pain",
        ],
        "properties": {
            "detections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["class", "confidence"],
                    "properties": {
                        "class": {"enum": [c["id"] for c in rules["detection_classes"]]},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                },
            },
            "photo_status": {"enum": [s["id"] for s in rules["photo_statuses"]]},
            "symptoms": {
                "type": "object",
                "additionalProperties": False,
                "required": [q["id"] for q in rules["questions"]],
                "properties": {
                    q["id"]: {"enum": list(rules["answers"])} for q in rules["questions"]
                },
            },
            "age_months": {
                "oneOf": [
                    {"type": "null"},
                    {
                        "type": "integer",
                        "minimum": limits["age_months_min"],
                        "maximum": limits["age_months_max"],
                    },
                ]
            },
            "pain": {
                "oneOf": [
                    {"const": "unknown"},
                    {"type": "integer", "minimum": scale["min"], "maximum": scale["max"]},
                ]
            },
        },
    }


def build_output_schema(rules: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "$schema": SCHEMA_DIALECT,
        "$id": "https://dentisai.local/triage/output.schema.json",
        "title": f"Triage engine output (rules {rules['version']})",
        "description": "Generated from rules.yaml by triage.export.",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "level",
            "reasons",
            "lesions",
            "advice_ids",
            "referral_template_id",
            "rules_version",
        ],
        "properties": {
            "level": {"enum": [level["id"] for level in rules["levels"]]},
            "reasons": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "text"],
                    "properties": {
                        "id": {"enum": [r["id"] for r in rules["rules"]]},
                        "text": {"type": "string"},
                    },
                },
            },
            "lesions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "detection_index",
                        "class",
                        "dentition",
                        "confidence",
                        "counted",
                    ],
                    "properties": {
                        "detection_index": {"type": "integer", "minimum": 0},
                        "class": {"enum": [c["id"] for c in rules["detection_classes"]]},
                        "dentition": {"enum": ["primary", "permanent"]},
                        "confidence": {"type": "number"},
                        "counted": {"type": "boolean"},
                    },
                },
            },
            "advice_ids": {
                "type": "array",
                "uniqueItems": True,
                "allOf": [{"contains": {"const": a}} for a in rules["always_advice"]],
                "items": {"enum": [a["id"] for a in rules["advice"]]},
            },
            "referral_template_id": {
                "enum": [level["referral_template_id"] for level in rules["levels"]]
            },
            "rules_version": {"const": rules["version"]},
        },
    }


def build_artifacts(rules: Mapping[str, Any]) -> dict[str, bytes]:
    files = {
        "rules.json": to_json_bytes(rules),
        "input.schema.json": to_json_bytes(build_input_schema(rules)),
        "output.schema.json": to_json_bytes(build_output_schema(rules)),
    }
    sums = "".join(f"{hashlib.sha256(body).hexdigest()}  {name}\n" for name, body in files.items())
    files["SHA256SUMS"] = sums.encode("utf-8")
    return files


def stale_files(dist_dir: Path, artifacts: Mapping[str, bytes]) -> list[str]:
    return [
        name
        for name, body in artifacts.items()
        if not (dist_dir / name).is_file() or (dist_dir / name).read_bytes() != body
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export rules.yaml to dist/ JSON artefacts.")
    parser.add_argument("--rules", type=Path, default=RULES_PATH)
    parser.add_argument("--dist", type=Path, default=DIST_DIR)
    parser.add_argument("--check", action="store_true", help="fail if dist/ is out of date")
    args = parser.parse_args(argv)

    artifacts = build_artifacts(load_rules(args.rules))
    if args.check:
        stale = stale_files(args.dist, artifacts)
        for name in stale:
            print(f"STALE: {args.dist / name} (run: uv run python -m triage.export)")
        return 1 if stale else 0
    args.dist.mkdir(parents=True, exist_ok=True)
    for name, body in artifacts.items():
        (args.dist / name).write_bytes(body)
        print(f"wrote {args.dist / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
