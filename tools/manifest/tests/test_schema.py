from __future__ import annotations

import copy
from typing import Any

import pytest

from conftest import sample_doc
from dentisai_manifest.canonical import JsonValue
from dentisai_manifest.errors import ErrorCode, ManifestError
from dentisai_manifest.schema import (
    contains_placeholder,
    parse_app_version,
    peek_schema_version,
    validate_manifest,
)

KID = "0123456789abcdef"


def doc(**over: JsonValue) -> dict[str, JsonValue]:
    return sample_doc(KID, **over)


def schema_error(d: Any, code: ErrorCode = ErrorCode.E_SCHEMA) -> str:
    with pytest.raises(ManifestError) as ei:
        validate_manifest(d)
    assert ei.value.code is code, ei.value
    return ei.value.message


def test_valid_doc_and_summary() -> None:
    m = validate_manifest(doc(expires_at="2026-12-01T00:00:00Z"))
    assert m.sequence == 3
    assert [a.name for a in m.artifacts()] == [
        "model",
        "model.labels",
        "rules",
        "recommender",
    ]
    s = m.summary()
    assert s["model_version"] == "1.0.0"
    assert s["expires_at"] == "2026-12-01T00:00:00Z"
    assert validate_manifest(doc()).summary()["expires_at"] is None


@pytest.mark.parametrize(
    ("over", "fragment"),
    [
        ({"sequence": 0}, "sequence"),
        ({"sequence": True}, "integer"),
        ({"sequence": "1"}, "integer"),
        ({"created_at": "2026-10-08T00:00:00+00:00"}, "created_at"),
        ({"created_at": "2026-10-08T00:00:00.123Z"}, "created_at"),
        ({"created_at": "2026-02-30T00:00:00Z"}, "calendar"),
        ({"created_at": 5}, "string"),
        ({"expires_at": "2026-10-08T00:00:00Z"}, "later"),
        ({"expires_at": "2026-10-07T00:00:00Z"}, "later"),
        ({"min_app_version": "1.0"}, "min_app_version"),
        ({"min_app_version": "01.0.0"}, "min_app_version"),
        ({"min_app_version": "1.0.0-beta"}, "min_app_version"),
        ({"signer_key_id": "ABCDEF0123456789"}, "signer_key_id"),
        ({"signer_key_id": "0123"}, "signer_key_id"),
        ({"channel": "staging"}, "channel"),
        ({"model": "x"}, "object"),
        ({"extra": 1}, "unknown"),
    ],
)
def test_top_level_errors(over: dict[str, JsonValue], fragment: str) -> None:
    assert fragment in schema_error(doc(**over))


def test_missing_required() -> None:
    d = doc()
    del d["recommender"]
    assert "missing" in schema_error(d)


@pytest.mark.parametrize(
    ("comp", "field", "value", "fragment"),
    [
        ("model", "version", "", "version"),
        ("model", "version", "a/b", "version"),
        ("model", "license", "", "license"),
        ("model", "license", "x" * 257, "license"),
        ("model", "url", "http://abc.supabase.co/x", "https"),
        ("model", "url", "https://abc.supabase.co/x?y=1", "https"),
        ("model", "url", "https://user@abc.supabase.co/x", "https"),
        ("model", "url", "https://ABC.supabase.co/x", "https"),
        ("model", "url", "https://abc.supabase.co/a/../b", "segments"),
        ("model", "url", "https://abc.supabase.co/a//b", "segments"),
        ("model", "url", "https://abc.supabase.co/a/./b", "segments"),
        ("model", "url", "https://abc.supabase.co/a/", "segments"),
        ("model", "sha256", "A" * 64, "sha256"),
        ("model", "labels_sha256", "0" * 63, "sha256"),
        ("model", "size_bytes", 0, "size_bytes"),
        ("model", "size_bytes", 50 * 1024 * 1024 + 1, "size_bytes"),
        ("model", "labels_size_bytes", 1024 * 1024 + 1, "size_bytes"),
        ("rules", "size_bytes", 2 * 1024 * 1024 + 1, "size_bytes"),
        ("recommender", "size_bytes", -1, "size_bytes"),
        ("rules", "license", "MIT", "unknown"),
    ],
)
def test_component_errors(comp: str, field: str, value: JsonValue, fragment: str) -> None:
    d = copy.deepcopy(doc())
    c = d[comp]
    assert isinstance(c, dict)
    c[field] = value
    assert fragment in schema_error(d)


def test_component_missing_field() -> None:
    d = copy.deepcopy(doc())
    c = d["rules"]
    assert isinstance(c, dict)
    del c["sha256"]
    assert "missing" in schema_error(d)


def test_schema_version_handling() -> None:
    assert "object" in schema_error([1])
    assert "schema_version" in schema_error({"sequence": 1})
    schema_error(doc(schema_version=2), ErrorCode.E_SCHEMA_VERSION_UNSUPPORTED)
    schema_error(doc(schema_version=0))
    assert peek_schema_version(doc()) == 1


def test_contains_placeholder() -> None:
    assert contains_placeholder({"a": ["x", {"b": "PLACEHOLDER"}]})
    assert not contains_placeholder({"a": [1, None, "placeholder"]})


def test_parse_app_version() -> None:
    assert parse_app_version("10.2.3") > parse_app_version("9.99.99")
    with pytest.raises(ValueError, match=r"MAJOR\.MINOR\.PATCH"):
        parse_app_version("1.2")
