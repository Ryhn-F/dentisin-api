"""Detection of unreviewed / placeholder content in artifacts before signing.

Markers (SPEC.md section 7.2):
- the substrings ``PLACEHOLDER`` or ``PENDING_DRG`` anywhere in a text artifact;
- in JSON artifacts, any key ``license_verified`` or ``verified`` whose value is ``false``;
- in JSON artifacts, any key ``status`` whose value is one of ``needs_review``,
  ``DRAFT``, ``PROPOSED``, ``PLACEHOLDER`` (case-sensitive; triage/recommender/ml
  exporters use these for unreviewed content).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .schema import PLACEHOLDER_MARKER

FALSE_FLAG_KEYS: Final = frozenset({"license_verified", "verified"})
REVIEW_STATUS_VALUES: Final = frozenset({"needs_review", "DRAFT", "PROPOSED", "PLACEHOLDER"})
TEXT_MARKERS: Final = (PLACEHOLDER_MARKER, "PENDING_DRG")


@dataclass(frozen=True)
class Finding:
    file: str
    where: str
    reason: str

    def __str__(self) -> str:
        return f"{self.file} {self.where}: {self.reason}"


def _walk(value: object, pointer: str, file: str, out: list[Finding]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            p = f"{pointer}/{k}"
            if k in FALSE_FLAG_KEYS and v is False:
                out.append(Finding(file, p, f'"{k}": false'))
            if k == "status" and v in REVIEW_STATUS_VALUES:
                out.append(Finding(file, p, f'"status": "{v}"'))
            _walk(v, p, file, out)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _walk(v, f"{pointer}/{i}", file, out)


def scan_text(text: str, file: str, *, is_json: bool) -> list[Finding]:
    out: list[Finding] = []
    for marker in TEXT_MARKERS:
        count = text.count(marker)
        if count:
            out.append(Finding(file, "(text)", f"contains {marker!r} x{count}"))
    if is_json:
        try:
            data = json.loads(text)
        except ValueError:
            out.append(Finding(file, "(json)", "is not valid JSON"))
        else:
            _walk(data, "", file, out)
    return out


def scan_file(path: Path) -> list[Finding]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return scan_text(text, str(path), is_json=path.suffix.lower() == ".json")
