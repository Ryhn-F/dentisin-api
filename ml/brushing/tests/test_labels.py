from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

import synthetic as syn
from brushing_score import score_session
from rules_loader import Rules
from validate_labels import frames_from_session, load_schema, main, validate_session

UUID = "123e4567-e89b-42d3-a456-426614174000"


def make_session(subject_type: str = "adult_volunteer") -> dict[str, Any]:
    frames = [f.to_dict() for f in syn.vigorous_brushing(6, seed=1)]
    child = subject_type == "child"
    return {
        "schema_version": "0.1.0",
        "session_id": UUID,
        "subject": {
            "pseudonym_id": "a1b2c3d4-e5f6",
            "subject_type": subject_type,
            "age_band": "9-12" if child else "adult",
        },
        "consent": {
            "consent_text_version": "consent-b-v0-draft",
            "consent_types": ["a", "b"],
            "guardian_consent": child,
            "assent": True if child else None,
            "collected_at_utc": "2026-10-08T03:00:00Z",
            "ethics_approval_ref": "KEPK-XXXX [ISI]" if child else None,
        },
        "recording": {
            "camera": "front",
            "mirrored_preview": True,
            "nominal_fps": 10,
            "device_class": "mid",
            "extractor": {"name": "synthetic", "version": "0"},
            "started_utc": "2026-10-08T03:01:00+00:00",
        },
        "privacy": {
            "contains_video": False,
            "contains_frames": False,
            "contains_raw_face_mesh": False,
        },
        "observers": [{"observer_id": "OBS-01", "role": "dentist"}],
        "frames": frames,
        "segments": [
            {
                "observer_id": "OBS-01",
                "start_ms": 0,
                "end_ms": 3000,
                "is_brushing": True,
                "sextant": "upper_front",
            },
            {
                "observer_id": "OBS-01",
                "start_ms": 3000,
                "end_ms": 5000,
                "is_brushing": False,
                "sextant": "none",
            },
        ],
    }


def test_schema_file_is_valid_json_schema() -> None:
    from jsonschema import Draft202012Validator

    Draft202012Validator.check_schema(load_schema())


def test_valid_adult_and_child_sessions() -> None:
    assert validate_session(make_session()) == []
    assert validate_session(make_session("child")) == []


def mutated(mutate: Any, subject_type: str = "adult_volunteer") -> list[str]:
    data = copy.deepcopy(make_session(subject_type))
    mutate(data)
    return validate_session(data)


@pytest.mark.parametrize(
    ("mutate", "child", "needle"),
    [
        (lambda d: d["consent"].update(ethics_approval_ref=None), True, "ethics_approval_ref"),
        (lambda d: d["consent"].update(ethics_approval_ref=""), True, "ethics_approval_ref"),
        (lambda d: d["consent"].update(guardian_consent=False), True, "guardian_consent"),
        (lambda d: d["consent"].update(assent=None), True, "assent"),
        (lambda d: d["consent"].update(consent_types=["a"]), True, "consent_types"),
        (lambda d: d["privacy"].update(contains_video=True), False, "contains_video"),
        (lambda d: d["privacy"].update(contains_frames=True), False, "contains_frames"),
        (lambda d: d["frames"][0].update(image="data:image/jpeg;base64,AAAA"), False, "image"),
        (lambda d: d["frames"][0].update(landmarks=[[0, 0]] * 468), False, "landmarks"),
        (lambda d: d["subject"].update(pseudonym_id="Budi Santoso"), False, "pseudonym_id"),
        (lambda d: d["subject"].update(name="Budi"), False, "name"),
        (lambda d: d.update(session_id="not-a-uuid"), False, "session_id"),
        (lambda d: d.update(schema_version="9"), False, "schema_version"),
        (lambda d: d["recording"].update(camera="back"), False, "camera"),
        (lambda d: d["frames"][0].update(face_conf=1.5), False, "face_conf"),
        (lambda d: d["frames"][0].update(mouth_center=[0.5]), False, "mouth_center"),
        (lambda d: d["segments"][1].update(sextant="upper_left"), False, "sextant"),
        (lambda d: d["segments"][0].update(sextant="none"), False, "sextant"),
        (lambda d: d["observers"][0].update(observer_id="Dr. Ani"), False, "observer_id"),
        (lambda d: d["frames"].clear(), False, "frames"),
        (lambda d: d.pop("segments"), False, "segments"),
    ],
)
def test_schema_violations(mutate: Any, child: bool, needle: str) -> None:
    errors = mutated(mutate, "child" if child else "adult_volunteer")
    assert errors, "expected a validation error"
    assert any(needle in e for e in errors), errors


def test_non_increasing_timestamps_rejected() -> None:
    errors = mutated(lambda d: d["frames"][3].update(t_ms=d["frames"][2]["t_ms"]))
    assert any("strictly after" in e for e in errors)


def test_face_and_hand_consistency() -> None:
    def break_face(d: dict[str, Any]) -> None:
        d["frames"][1]["mouth_center"] = None

    def phantom_face(d: dict[str, Any]) -> None:
        d["frames"][1].update(face_present=False)

    def break_hand(d: dict[str, Any]) -> None:
        d["frames"][1]["hand_center"] = None

    def phantom_hand(d: dict[str, Any]) -> None:
        d["frames"][1].update(hand_present=False)

    assert any("face_present requires" in e for e in mutated(break_face))
    assert any("no face" in e for e in mutated(phantom_face))
    assert any("hand_present requires" in e for e in mutated(break_hand))
    assert any("no hand" in e for e in mutated(phantom_hand))


def test_segment_semantics() -> None:
    last = make_session()["frames"][-1]["t_ms"]

    def bad_order(d: dict[str, Any]) -> None:
        d["segments"][0].update(start_ms=2000, end_ms=1000)

    def out_of_range(d: dict[str, Any]) -> None:
        d["segments"][0].update(end_ms=last + 1000)

    def unknown_observer(d: dict[str, Any]) -> None:
        d["segments"][0].update(observer_id="OBS-99")

    def overlap(d: dict[str, Any]) -> None:
        d["segments"][1].update(start_ms=2500)

    def duplicate_observer(d: dict[str, Any]) -> None:
        d["observers"].append({"observer_id": "OBS-01", "role": "other"})

    assert any("greater than start_ms" in e for e in mutated(bad_order))
    assert any("outside the frame range" in e for e in mutated(out_of_range))
    assert any("not declared" in e for e in mutated(unknown_observer))
    assert any("overlapping" in e for e in mutated(overlap))
    assert any("duplicate observer_id" in e for e in mutated(duplicate_observer))


def test_two_observers_may_overlap_each_other() -> None:
    def add_second(d: dict[str, Any]) -> None:
        d["observers"].append({"observer_id": "OBS-02", "role": "trained_observer"})
        d["segments"].append(
            {
                "observer_id": "OBS-02",
                "start_ms": 1000,
                "end_ms": 4000,
                "is_brushing": True,
                "sextant": "unknown",
            }
        )

    assert mutated(add_second) == []


def test_timestamps_must_carry_offset_and_parse() -> None:
    naive = mutated(lambda d: d["consent"].update(collected_at_utc="2026-10-08T03:00:00"))
    assert any("UTC offset" in e for e in naive)
    garbage = mutated(lambda d: d["recording"].update(started_utc="yesterday-ish"))
    assert any("ISO 8601" in e for e in garbage)


def test_frames_feed_the_scorer(short_rules: Rules) -> None:
    frames = frames_from_session(make_session())
    assert score_session(frames, short_rules).active_ms > 0


# ---------------------------------------------------------------- CLI
def test_cli_ok_and_invalid(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    good = tmp_path / "good.json"
    good.write_text(json.dumps(make_session()), encoding="utf-8")
    bad_data = make_session()
    bad_data["privacy"]["contains_video"] = True
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(bad_data), encoding="utf-8")

    assert main([str(good)]) == 0
    assert "ok" in capsys.readouterr().out
    assert main([str(good), str(bad)]) == 1
    out = capsys.readouterr().out
    assert "INVALID" in out
    assert "contains_video" in out


def test_cli_unreadable_and_no_args(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert main([str(tmp_path / "missing.json")]) == 2
    assert main([str(broken)]) == 2
    assert main([]) == 2
    assert "usage" in capsys.readouterr().err
