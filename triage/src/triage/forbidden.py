"""Forbidden-phrase lint, parsed from the single source ``docs/style/forbidden_phrases.md``.

Location: env ``FORBIDDEN_PHRASES_PATH`` > shared original ``<repo>/docs/style/`` >
vendored copy ``backend/docs/style/`` (see ``triage.paths.forbidden_phrases_path``).

Parsing rules (keep the markdown table format stable):
* Every double-quoted string in the first column of the table is a forbidden
  phrase. ``…`` / ``...`` are stripped, ``XX`` means "any number".
* Double-quoted sentences on lines starting with ``Wajib`` are mandatory texts
  (e.g. "Hasil ini bukan diagnosis ...") and are allow-listed verbatim.
* Matching is case-insensitive and on whole words (a short forbidden word does not
  match inside a longer word that contains it). "bebas karies" is forbidden outright
  (stricter than the guide, which allows it with a disclaimer).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from triage.paths import forbidden_phrases_path

_QUOTED = re.compile(r'"([^"]+)"')


@dataclass(frozen=True)
class ForbiddenList:
    phrases: tuple[str, ...]
    patterns: tuple[re.Pattern[str], ...]
    mandatory: tuple[str, ...]


def _to_pattern(phrase: str) -> re.Pattern[str]:
    parts = [re.escape(part) for part in phrase.split("XX")]
    body = r"\d+".join(parts).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)


def parse_forbidden_markdown(markdown: str) -> ForbiddenList:
    phrases: list[str] = []
    mandatory: list[str] = []
    for raw in markdown.splitlines():
        line = raw.strip()
        if line.startswith("|"):
            first_cell = line.split("|")[1]
            for quoted in _QUOTED.findall(first_cell):
                phrase = quoted.replace("…", "").replace("...", "").strip()
                if phrase:
                    phrases.append(phrase)
        elif line.startswith("Wajib"):
            mandatory.extend(_QUOTED.findall(line))
    unique = tuple(dict.fromkeys(phrases))
    return ForbiddenList(unique, tuple(_to_pattern(p) for p in unique), tuple(mandatory))


def load_forbidden(path: Path | None = None) -> ForbiddenList:
    """Parse the forbidden list (default location: ``paths.forbidden_phrases_path()``)."""
    source = forbidden_phrases_path() if path is None else path
    return parse_forbidden_markdown(source.read_text(encoding="utf-8"))


def find_forbidden(text: str, forbidden: ForbiddenList) -> list[str]:
    """Forbidden phrases found in ``text`` (mandatory sentences are ignored)."""
    cleaned = text
    for sentence in forbidden.mandatory:
        cleaned = re.sub(re.escape(sentence), " ", cleaned, flags=re.IGNORECASE)
    return [
        phrase
        for phrase, pattern in zip(forbidden.phrases, forbidden.patterns, strict=True)
        if pattern.search(cleaned)
    ]


def iter_strings(node: object, path: str = "$") -> Iterator[tuple[str, str]]:
    """Yield ``(json_path, string)`` for every string value in a nested structure."""
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from iter_strings(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from iter_strings(value, f"{path}[{index}]")


def lint_document(document: object, forbidden: ForbiddenList) -> list[str]:
    """``path: phrase`` for every forbidden phrase in any string of ``document``."""
    return [
        f"{path}: '{phrase}'"
        for path, text in iter_strings(document)
        for phrase in find_forbidden(text, forbidden)
    ]
