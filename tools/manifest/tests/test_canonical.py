from __future__ import annotations

import pytest

from dentisai_manifest.canonical import (
    MAX_SAFE_INTEGER,
    canonicalize,
    is_printable_ascii,
    parse_canonical,
    parse_strict,
)
from dentisai_manifest.errors import ErrorCode, ManifestError


def rejects(fn: object, *args: object) -> ManifestError:
    with pytest.raises(ManifestError) as ei:
        fn(*args)  # type: ignore[operator]
    assert ei.value.code is ErrorCode.E_NOT_CANONICAL
    return ei.value


def test_sorted_keys_no_whitespace() -> None:
    assert canonicalize({"b": [1, {"d": None, "c": True}], "a": "x"}) == (
        b'{"a":"x","b":[1,{"c":true,"d":null}]}'
    )


def test_only_quote_and_backslash_are_escaped() -> None:
    assert canonicalize({"s": 'a"b\\c/d'}) == b'{"s":"a\\"b\\\\c/d"}'


def test_false_and_ints() -> None:
    assert canonicalize([False, 0, -5, MAX_SAFE_INTEGER]) == b"[false,0,-5,9007199254740991]"


@pytest.mark.parametrize(
    "value",
    [
        1.0,
        float("nan"),
        {"a": 0.5},
        {"A": 1},
        {"_x": 1},
        {1: 2},
        {"a": "é"},
        {"a": "line\n"},
        {"a": MAX_SAFE_INTEGER + 1},
        {"a": -MAX_SAFE_INTEGER - 1},
        (1, 2),
        b"bytes",
    ],
)
def test_canonicalize_rejects(value: object) -> None:
    rejects(canonicalize, value)


def test_depth_limit() -> None:
    ok: object = {}
    for _ in range(15):
        ok = [ok]
    assert canonicalize(ok)  # type: ignore[arg-type]
    rejects(canonicalize, [ok])


def test_parse_strict_accepts_whitespace_and_reorders() -> None:
    assert canonicalize(parse_strict(' {"b" : 1,\n "a":[ ]} ')) == b'{"a":[],"b":1}'


@pytest.mark.parametrize(
    "text",
    [
        '{"a":1,"a":2}',  # duplicate key
        '{"a":{"x":1,"x":1}}',  # nested duplicate
        '{"a":1.5}',
        '{"a":1.0}',
        '{"a":1e3}',
        '{"a":NaN}',
        '{"a":Infinity}',
        '{"a":-Infinity}',
        '{"a":-0}',
        '{"a":9007199254740992}',
        '{"a":"\\u00e9"}',
        '{"a":"\\ud800"}',
        "{not json",
    ],
)
def test_parse_strict_rejects(text: str) -> None:
    rejects(parse_strict, text)


def test_parse_strict_recursion_bomb() -> None:
    rejects(parse_strict, "[" * 100_000 + "]" * 100_000)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":1}\n',
        b' {"a":1}',
        b'{"a": 1}',
        b'{"b":1,"a":1}',
        b"\xef\xbb\xbf{}",
        b'{"a":"\\u0041"}',
        b'{"a":"\\/"}',
        '{"a":"é"}'.encode(),
    ],
)
def test_parse_canonical_rejects_non_canonical(raw: bytes) -> None:
    rejects(parse_canonical, raw)


def test_parse_canonical_roundtrip() -> None:
    raw = b'{"a":[true,false,null,-1,"q\\"\\\\"],"b":{}}'
    assert canonicalize(parse_canonical(raw)) == raw


def test_is_printable_ascii() -> None:
    assert is_printable_ascii(" ~")
    assert not is_printable_ascii("\x7f")
    assert not is_printable_ascii(b"\x1f")
