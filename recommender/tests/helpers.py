"""Shared helpers for the recommender tests."""

from __future__ import annotations

import copy
import json
import os
import re
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

PKG_DIR = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PKG_DIR.parent

REVIEWER = "Test Reviewer (tests only, not a real person)"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def forbidden_phrases_path() -> Path:
    """env FORBIDDEN_PHRASES_PATH, else ../docs/style (repo), else vendored backend/docs/style."""
    env = os.environ.get("FORBIDDEN_PHRASES_PATH")
    candidates = [Path(env)] if env else []
    candidates += [
        BACKEND_ROOT.parent / "docs" / "style" / "forbidden_phrases.md",
        BACKEND_ROOT / "docs" / "style" / "forbidden_phrases.md",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("forbidden_phrases.md not found; set FORBIDDEN_PHRASES_PATH")


def forbidden_phrases() -> list[str]:
    """Quoted phrases from the first column of the forbidden-phrases table (lower-cased)."""
    phrases: list[str] = []
    for line in forbidden_phrases_path().read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or line.startswith("|---"):
            continue
        first_cell = line.split("|")[1]
        for quoted in re.findall(r'"([^"]+)"', first_cell):
            phrase = quoted.replace("…", "").strip().casefold()
            if phrase and "xx" not in phrase:
                phrases.append(phrase)
    return phrases


def iter_strings(node: Any, skip_keys: frozenset[str] = frozenset()) -> Iterator[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, Mapping):
        for key, value in node.items():
            if key not in skip_keys:
                yield from iter_strings(value, skip_keys)
    elif isinstance(node, Sequence):
        for value in node:
            yield from iter_strings(value, skip_keys)


def approve_everything(rules: Mapping[str, Any]) -> dict[str, Any]:
    """Deep copy of ``rules`` with every reviewable row approved and the ppm decision made."""
    out = copy.deepcopy(dict(rules))

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "status" in node and "reviewed_by" in node:
                node["status"] = "approved"
                node["reviewed_by"] = REVIEWER
                node["reviewed_at"] = "2000-01-01"
            if "candidates" in node:
                node["decision_pending"] = False
                if node["candidates"]:
                    node["selected_candidate_id"] = node["candidates"][0]["id"]
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(out)
    return out


def verify_everything(products: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(dict(products))
    for item in out["products"]:
        item.update(
            verified=True,
            verified_at="2000-01-01",
            bpom_notification_no="TEST-ONLY",
            source="tests only",
            synthetic=False,
        )
    return out
