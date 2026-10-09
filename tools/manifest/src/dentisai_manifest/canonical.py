"""DentisAI Canonical JSON (DCJ-1): a strict subset of RFC 8785 (JCS).

For every value accepted here, the output is byte-identical to RFC 8785 JCS
and to ECMAScript ``JSON.stringify`` applied to an object whose keys were
inserted in sorted order. The subset exists so that a TypeScript verifier can
reproduce it with ~30 lines of code and no Unicode/float corner cases.

Accepted value space (anything else is rejected):
- object: keys match ``^[a-z][a-z0-9_]{0,63}$``, unique, emitted sorted by
  byte value (== UTF-16 code unit order for ASCII);
- array;
- string: only printable ASCII U+0020..U+007E; ``"`` -> ``\\"`` and
  ``\\`` -> ``\\\\``; no other escapes ever appear;
- integer: -(2^53-1) .. 2^53-1, shortest decimal form, never ``-0``;
- true / false / null;
- nesting depth <= MAX_DEPTH.
No floats, no exponents, no NaN/Infinity, no whitespace, no BOM, no trailing
newline. See SPEC.md section 3.
"""

from __future__ import annotations

import json
import re
from typing import NoReturn, TypeAlias

from .errors import ErrorCode, ManifestError

JsonValue: TypeAlias = bool | int | str | list["JsonValue"] | dict[str, "JsonValue"] | None

MAX_SAFE_INTEGER = 2**53 - 1
MAX_DEPTH = 16
KEY_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_INT_RE = re.compile(r"-?(0|[1-9][0-9]*)")


def _reject(message: str) -> NoReturn:
    raise ManifestError(ErrorCode.E_NOT_CANONICAL, message)


def is_printable_ascii(data: bytes | str) -> bool:
    """True iff every byte/char is in 0x20..0x7E."""
    if isinstance(data, str):
        return all(0x20 <= ord(c) <= 0x7E for c in data)
    return all(0x20 <= b <= 0x7E for b in data)


def _check_string(s: str, what: str) -> None:
    if not is_printable_ascii(s):
        _reject(f"{what} contains a character outside printable ASCII 0x20..0x7E")


def _encode_string(s: str) -> str:
    _check_string(s, "string")
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _encode(value: object, depth: int, out: list[str]) -> None:
    if depth > MAX_DEPTH:
        _reject(f"nesting deeper than {MAX_DEPTH}")
    # bool must be tested before int (bool is a subclass of int in Python).
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, int):
        if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            _reject("integer outside the IEEE-754 safe range +-(2^53-1)")
        out.append(str(value))
    elif isinstance(value, str):
        out.append(_encode_string(value))
    elif isinstance(value, list):
        out.append("[")
        for i, item in enumerate(value):
            if i:
                out.append(",")
            _encode(item, depth + 1, out)
        out.append("]")
    elif isinstance(value, dict):
        keys = list(value.keys())
        for k in keys:
            if not isinstance(k, str) or not KEY_RE.fullmatch(k):
                _reject(f"object key {k!r} does not match ^[a-z][a-z0-9_]{{0,63}}$")
        out.append("{")
        for i, k in enumerate(sorted(keys)):
            if i:
                out.append(",")
            out.append('"' + k + '":')
            _encode(value[k], depth + 1, out)
        out.append("}")
    else:
        _reject(f"unsupported JSON value type {type(value).__name__} (floats are forbidden)")


def canonicalize(value: JsonValue) -> bytes:
    """Serialize ``value`` to DCJ-1 canonical bytes (ASCII)."""
    out: list[str] = []
    _encode(value, 1, out)
    return "".join(out).encode("ascii")


def _no_duplicates(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for k, v in pairs:
        if k in result:
            _reject(f"duplicate object key {k!r}")
        result[k] = v
    return result


def _parse_int(token: str) -> int:
    if not _INT_RE.fullmatch(token) or token == "-0":  # noqa: S105
        _reject(f"integer token {token!r} is not in shortest form")
    value = int(token)
    if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
        _reject("integer outside the IEEE-754 safe range +-(2^53-1)")
    return value


def _parse_float(token: str) -> NoReturn:
    _reject(f"non-integer number {token!r} (floats/exponents are forbidden)")


def _parse_constant(token: str) -> NoReturn:
    _reject(f"{token} is not valid JSON")


def parse_strict(text: str) -> JsonValue:
    """Parse JSON text rejecting duplicate keys, floats, NaN/Infinity and unsafe ints.

    Whitespace between tokens IS accepted here (used for human-written input to
    the signer). Use :func:`parse_canonical` for anything received over the wire.
    """
    try:
        value: JsonValue = json.loads(
            text,
            object_pairs_hook=_no_duplicates,
            parse_int=_parse_int,
            parse_float=_parse_float,
            parse_constant=_parse_constant,
        )
    except ManifestError:
        raise
    except RecursionError:
        _reject("nesting too deep")
    except ValueError as exc:  # json.JSONDecodeError is a ValueError
        _reject(f"invalid JSON: {exc}")
    # Re-encode to apply the value-space rules (keys, strings, depth).
    canonicalize(value)
    return value


def parse_canonical(raw: bytes) -> JsonValue:
    """Parse wire bytes and require them to be exactly the DCJ-1 canonical form."""
    if not is_printable_ascii(raw):
        _reject("bytes outside printable ASCII 0x20..0x7E (BOM, newline, tab, non-ASCII?)")
    value = parse_strict(raw.decode("ascii"))
    if canonicalize(value) != raw:
        _reject("bytes are not in canonical form (key order, whitespace or escapes differ)")
    return value
