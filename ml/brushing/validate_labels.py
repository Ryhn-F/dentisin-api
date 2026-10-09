"""Validate a brushing label session file against ``label_schema.json`` plus semantic checks.

Usage:  python validate_labels.py session1.json [session2.json ...]
Exit code 0 = all valid, 1 = at least one invalid, 2 = unreadable input.

The files contain landmark-derived features only. This tool refuses anything that does not match
the schema exactly (``additionalProperties: false``), so image-like fields cannot slip in.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from brushing_score import Frame

SCHEMA_PATH = Path(__file__).with_name("label_schema.json")


def load_schema() -> dict[str, Any]:
    with SCHEMA_PATH.open(encoding="utf-8") as handle:
        schema: dict[str, Any] = json.load(handle)
    return schema


def _check_timestamp(value: str, where: str, errors: list[str]) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{where}: not an ISO 8601 date-time: {value!r}")
        return
    if parsed.tzinfo is None:
        errors.append(f"{where}: timestamp must carry a UTC offset (store UTC)")


def _frame_errors(frames: Sequence[Mapping[str, Any]]) -> list[str]:
    errors: list[str] = []
    prev: int | None = None
    for i, fr in enumerate(frames):
        t = fr["t_ms"]
        if prev is not None and t <= prev:
            errors.append(f"frames[{i}]: t_ms {t} is not strictly after previous {prev}")
        prev = t
        if fr["face_present"] and (fr["mouth_center"] is None or fr["face_scale"] is None):
            errors.append(f"frames[{i}]: face_present requires mouth_center and face_scale")
        if not fr["face_present"] and (
            fr["mouth_center"] is not None or fr["face_scale"] is not None
        ):
            errors.append(f"frames[{i}]: no face, but mouth_center/face_scale are set")
        if fr["hand_present"] and fr["hand_center"] is None:
            errors.append(f"frames[{i}]: hand_present requires hand_center")
        if not fr["hand_present"] and fr["hand_center"] is not None:
            errors.append(f"frames[{i}]: no hand, but hand_center is set")
    return errors


def _segment_errors(session: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    frames = session["frames"]
    first_t, last_t = frames[0]["t_ms"], frames[-1]["t_ms"]
    declared = [o["observer_id"] for o in session["observers"]]
    if len(set(declared)) != len(declared):
        errors.append("observers: duplicate observer_id")
    by_observer: dict[str, list[tuple[int, int]]] = {}
    for i, seg in enumerate(session["segments"]):
        start, end = seg["start_ms"], seg["end_ms"]
        if end <= start:
            errors.append(f"segments[{i}]: end_ms must be greater than start_ms")
        if start < first_t or end > last_t:
            errors.append(
                f"segments[{i}]: [{start}, {end}] outside the frame range [{first_t}, {last_t}]"
            )
        if seg["observer_id"] not in declared:
            errors.append(f"segments[{i}]: observer_id {seg['observer_id']!r} is not declared")
        by_observer.setdefault(seg["observer_id"], []).append((start, end))
    for observer, spans in by_observer.items():
        spans.sort()
        for (_, prev_end), (next_start, _) in pairwise(spans):
            if next_start < prev_end:
                errors.append(f"segments: overlapping segments for {observer}")
                break
    return errors


def validate_session(session: Any) -> list[str]:
    """Return a list of human-readable problems; an empty list means the session is valid."""
    validator = Draft202012Validator(load_schema())
    errors = [
        f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in sorted(
            validator.iter_errors(session), key=lambda e: list(map(str, e.absolute_path))
        )
    ]
    if errors:
        return errors  # semantic checks assume a schema-valid document
    errors.extend(_frame_errors(session["frames"]))
    errors.extend(_segment_errors(session))
    _check_timestamp(session["consent"]["collected_at_utc"], "consent.collected_at_utc", errors)
    _check_timestamp(session["recording"]["started_utc"], "recording.started_utc", errors)
    return errors


def frames_from_session(session: Mapping[str, Any]) -> list[Frame]:
    """Convert a (validated) session's frame records for ``brushing_score.score_session``."""
    return [Frame.from_dict(f) for f in session["frames"]]


def main(argv: Sequence[str] | None = None) -> int:
    paths = list(sys.argv[1:] if argv is None else argv)
    if not paths:
        print("usage: validate_labels.py FILE [FILE ...]", file=sys.stderr)
        return 2
    status = 0
    for raw in paths:
        try:
            with Path(raw).open(encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"{raw}: cannot read: {exc}", file=sys.stderr)
            return 2
        problems = validate_session(data)
        if problems:
            status = 1
            print(f"{raw}: INVALID")
            for problem in problems:
                print(f"  - {problem}")
        else:
            print(f"{raw}: ok")
    return status


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
