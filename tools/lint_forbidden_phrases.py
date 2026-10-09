#!/usr/bin/env python3
"""Lint user-facing text against docs/style/forbidden_phrases.md (stdlib only).

Source of the list (first match wins): env ``FORBIDDEN_PHRASES_PATH``; the shared
``<BACKEND_ROOT>/../docs/style/forbidden_phrases.md`` if it exists (monorepo); else the vendored
copy ``<BACKEND_ROOT>/docs/style/forbidden_phrases.md``. When both the shared and the vendored
copy exist and differ, the run fails with exit code 3 (``--check-sync`` does only that check).

The markdown table is the single source of truth. This tool parses the first
column of the table ("Terlarang") and turns every quoted phrase into a regex:

* matching is case-insensitive and whitespace-tolerant;
* a trailing "..." / ellipsis means "anything may follow" (the phrase itself is matched);
* "XX" inside a phrase stands for a number (``akurasi XX%``);
* phrases are matched on word boundaries ("aman" does not hit "keamanan"/"amankan");
* a row marked "tanpa disclaimer" is only reported when no disclaimer appears within
  +-2 lines (``bebas karies`` is allowed next to "bukan diagnosis ...").

Because the mandatory result-screen disclaimer itself contains "diagnosis"/"aman"-like
words in negated form, a few *context exemptions* mask safe negations ("bukan diagnosis",
"tidak aman", "belum pasti"). They are heuristics and are listed in the output.

Rules that cannot be checked mechanically (unquoted rows, required-text rules, product
brand neutrality) are printed on every run so nobody assumes they are covered.

Exit codes: 0 clean, 1 forbidden phrase found, 2 usage/configuration error,
3 shared and vendored phrase lists differ.
Suppress one line with the pragma ``forbidden-phrases:ignore`` in a comment.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TextIO

DEFAULT_PHRASES_FILE = "docs/style/forbidden_phrases.md"  # relative to BACKEND_ROOT (vendored)
PHRASES_ENV = "FORBIDDEN_PHRASES_PATH"
DEFAULT_SCAN_PATHS = ("triage", "recommender", "supabase/seed", "tools/tts")
DEFAULT_EXTENSIONS = (".json", ".yaml", ".yml", ".csv", ".sql", ".txt")
SKIP_DIR_NAMES = frozenset(
    {".git", "node_modules", ".venv", "__pycache__", ".mypy_cache", ".pytest_cache", "docs"}
)
MAX_FILE_BYTES = 2_000_000
IGNORE_PRAGMA = "forbidden-phrases:ignore"
DISCLAIMER_WINDOW = 2

DISCLAIMER_RE = re.compile(
    r"bukan\s+(?:merupakan\s+)?(?:suatu\s+)?diagnosis"
    r"|tidak\s+menggantikan\s+pemeriksaan\s+dokter\s+gigi",
    re.IGNORECASE,
)

# Context exemptions: spans that look like a forbidden phrase but are the safe (negated or
# mandatory-disclaimer) form. Deliberately narrow. Keep in sync with the tests.
CONTEXT_EXEMPTIONS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "bukan diagnosis (mandatory disclaimer)",
        re.compile(
            r"(?<!\w)bukan\s+(?:merupakan\s+|suatu\s+|sebuah\s+|pengganti\s+)*(?:hasil\s+)?"
            r"diagnosis(?!\w)",
            re.IGNORECASE,
        ),
    ),
    (
        "negated 'pasti' (belum/tidak/bukan pasti)",
        re.compile(r"(?<!\w)(?:belum|tidak|bukan)\s+pasti(?!\w)", re.IGNORECASE),
    ),
    (
        "negated 'dipastikan' (belum/tidak [dapat|bisa] dipastikan)",
        re.compile(
            r"(?<!\w)(?:belum|tidak)\s+(?:(?:dapat|bisa)\s+)?dipastikan(?!\w)", re.IGNORECASE
        ),
    ),
    (
        "negated 'aman' (tidak/kurang/belum/bukan aman)",
        re.compile(r"(?<!\w)(?:tidak|kurang|belum|bukan)\s+aman(?!\w)", re.IGNORECASE),
    ),
)

# Always reported as "not mechanized" in addition to what the parser discovers.
STATIC_LIMITATIONS = (
    "Inflected/prefixed forms and synonyms not listed in the table (e.g. 'mendiagnosis', "
    "'diagnosa', '100 persen') are not matched.",
    "Phrases split across lines (folded YAML, wrapped text) are not matched.",
    "Context exemptions are heuristics: " + "; ".join(name for name, _ in CONTEXT_EXEMPTIONS) + ".",
    "Required text (result-screen disclaimer, toothpaste disclaimer) is not verified; "
    "this tool only finds forbidden text.",
)

_QUOTE_RE = re.compile(r'["“”]([^"“”]+)["“”]')
_ELLIPSES = ("…", "...")
_CLITIC = r"(?:nya|lah|kah)?"


@dataclass(frozen=True)
class Phrase:
    text: str
    regex: re.Pattern[str]


@dataclass(frozen=True)
class Rule:
    rule_id: str
    phrases: tuple[Phrase, ...]
    reason: str
    replacement: str
    needs_disclaimer: bool


@dataclass(frozen=True)
class Unmechanized:
    where: str
    text: str
    why: str


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    col: int
    rule_id: str
    phrase: str
    matched: str
    reason: str
    replacement: str


class LintConfigError(Exception):
    """The phrase list is missing or could not be parsed into any rule."""


def _phrase_regex(phrase: str) -> re.Pattern[str]:
    core = phrase
    for ell in _ELLIPSES:
        core = core.replace(ell, "")
    core = core.strip()
    tokens = core.split()
    if not tokens:
        raise ValueError(f"empty phrase: {phrase!r}")
    parts: list[str] = []
    for tok in tokens:
        pieces = tok.split("XX")
        parts.append(r"\d+(?:[.,]\d+)?".join(re.escape(p) for p in pieces))
    body = r"\s+".join(parts).replace("%", r"\s*%")
    lead = ""
    if core[0].isalnum() or core[0] == "_":
        lead = r"(?<![\w.,])" if core[0].isdigit() else r"(?<!\w)"
    trail = ""
    if core[-1].isalpha() or core[-1] == "_":
        trail = _CLITIC + r"(?!\w)"
    elif core[-1].isdigit():
        trail = r"(?![\w])"
    return re.compile(lead + body + trail, re.IGNORECASE)


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_separator(cells: Sequence[str]) -> bool:
    return all(c and set(c) <= set("-: ") for c in cells)


def parse_rules(markdown: str) -> tuple[list[Rule], list[Unmechanized]]:
    """Parse the forbidden-phrases markdown into rules plus a list of un-mechanized items."""
    rules: list[Rule] = []
    skipped: list[Unmechanized] = []
    in_table = False
    row_no = 0
    for raw in markdown.splitlines():
        line = raw.strip()
        if line.startswith("|"):
            cells = _split_row(line)
            if not in_table:
                if cells and cells[0].lower() == "terlarang":
                    in_table = True
                continue
            if _is_separator(cells) or len(cells) < 2:
                continue
            row_no += 1
            rule_id = f"FP{row_no:02d}"
            first = cells[0]
            reason = cells[1] if len(cells) > 1 else ""
            replacement = cells[2] if len(cells) > 2 else ""
            quoted = [q.strip() for q in _QUOTE_RE.findall(first)]
            remainder = _QUOTE_RE.sub(" ", first)
            needs_disclaimer = bool(re.search(r"tanpa\s+disclaimer", remainder, re.IGNORECASE))
            remainder = re.sub(r"tanpa\s+disclaimer", " ", remainder, flags=re.IGNORECASE)
            remainder = re.sub(r"\([^)]*\)", " ", remainder)
            remainder = re.sub(r"\b(?:dan|atau)\b", " ", remainder, flags=re.IGNORECASE)
            remainder = re.sub(r"[\s,;]+", " ", remainder).strip()
            if quoted:
                rules.append(
                    Rule(
                        rule_id=rule_id,
                        phrases=tuple(Phrase(q, _phrase_regex(q)) for q in quoted),
                        reason=reason,
                        replacement=replacement,
                        needs_disclaimer=needs_disclaimer,
                    )
                )
            if remainder:
                skipped.append(
                    Unmechanized(
                        rule_id,
                        remainder,
                        "no quoted phrase to match" if not quoted else "unquoted part of the row",
                    )
                )
        else:
            if in_table and line == "":
                in_table = False
            if line.startswith("Wajib"):
                skipped.append(
                    Unmechanized("required-text", line, "positive requirement, not checked")
                )
    return rules, skipped


def load_rules(path: Path) -> tuple[list[Rule], list[Unmechanized]]:
    if not path.is_file():
        raise LintConfigError(f"Phrase list not found: {path}")
    rules, skipped = parse_rules(path.read_text(encoding="utf-8"))
    if not rules:
        raise LintConfigError(f"No rules could be parsed from {path} (table format changed?)")
    return rules, skipped


def _mask(line: str) -> str:
    masked = line.replace(" ", " ")
    for _name, pattern in CONTEXT_EXEMPTIONS:
        masked = pattern.sub(lambda m: " " * len(m.group(0)), masked)
    return masked


def scan_text(text: str, rules: Sequence[Rule], source: str = "<text>") -> list[Hit]:
    """Return all forbidden-phrase hits in ``text`` (line/col are 1-based)."""
    lines = text.splitlines()
    hits: list[Hit] = []
    for idx, line in enumerate(lines):
        if IGNORE_PRAGMA in line:
            continue
        masked = _mask(line)
        window: str | None = None
        for rule in rules:
            for phrase in rule.phrases:
                for m in phrase.regex.finditer(masked):
                    if rule.needs_disclaimer:
                        if window is None:
                            lo = max(0, idx - DISCLAIMER_WINDOW)
                            window = "\n".join(lines[lo : idx + DISCLAIMER_WINDOW + 1])
                        if DISCLAIMER_RE.search(window):
                            continue
                    hits.append(
                        Hit(
                            path=source,
                            line=idx + 1,
                            col=m.start() + 1,
                            rule_id=rule.rule_id,
                            phrase=phrase.text,
                            matched=line[m.start() : m.end()],
                            reason=rule.reason,
                            replacement=rule.replacement,
                        )
                    )
    hits.sort(key=lambda h: (h.line, h.col, h.rule_id))
    return hits


def iter_files(
    paths: Iterable[Path], extensions: Sequence[str], include_docs: bool = False
) -> Iterator[Path]:
    """Yield files to scan. Explicit file arguments are always scanned."""
    skip = SKIP_DIR_NAMES - ({"docs"} if include_docs else set())
    for root in paths:
        if root.is_file():
            yield root
            continue
        stack = [root]
        while stack:
            current = stack.pop()
            for entry in sorted(current.iterdir()):
                if entry.is_dir():
                    if entry.name not in skip:
                        stack.append(entry)
                elif entry.suffix.lower() in extensions:
                    yield entry


def scan_paths(
    paths: Iterable[Path],
    rules: Sequence[Rule],
    extensions: Sequence[str] = DEFAULT_EXTENSIONS,
    include_docs: bool = False,
    display_root: Path | None = None,
) -> tuple[list[Hit], int, list[str]]:
    """Scan files; returns (hits, files_scanned, notes)."""
    hits: list[Hit] = []
    scanned = 0
    notes: list[str] = []
    for file in iter_files(paths, extensions, include_docs):
        try:
            if file.stat().st_size > MAX_FILE_BYTES:
                notes.append(f"skipped (larger than {MAX_FILE_BYTES} bytes): {file}")
                continue
            text = file.read_text(encoding="utf-8-sig")
        except (UnicodeDecodeError, OSError) as exc:
            notes.append(f"skipped (unreadable: {type(exc).__name__}): {file}")
            continue
        scanned += 1
        shown = file
        if display_root is not None:
            try:
                shown = file.resolve().relative_to(display_root.resolve())
            except ValueError:
                shown = file
        hits.extend(scan_text(text, rules, shown.as_posix()))
    return hits, scanned, notes


def backend_root() -> Path:
    """BACKEND_ROOT: the folder containing tools/ (this file lives in tools/)."""
    return Path(__file__).resolve().parent.parent


def _normalized(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n").lstrip(b"\xef\xbb\xbf")


def resolve_phrases_path(root: Path, environ: Mapping[str, str] | None = None) -> Path:
    """Env override, else shared ``root/../docs/style`` if present, else vendored copy."""
    env = os.environ if environ is None else environ
    override = env.get(PHRASES_ENV, "").strip()
    if override:
        return Path(override)
    shared = root.parent / DEFAULT_PHRASES_FILE
    if shared.is_file():
        return shared
    return root / DEFAULT_PHRASES_FILE


def check_sync(root: Path) -> str | None:
    """Return an error message if the shared and vendored phrase lists both exist and differ."""
    shared = root.parent / DEFAULT_PHRASES_FILE
    vendored = root / DEFAULT_PHRASES_FILE
    if shared.is_file() and vendored.is_file() and _normalized(shared) != _normalized(vendored):
        return (
            f"forbidden_phrases.md differs between the shared copy ({shared}) and the vendored "
            f"copy ({vendored}). Copy the newer one over the older, then re-run."
        )
    return None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Lint text for phrases forbidden by docs/style/forbidden_phrases.md.",
        epilog="Exit codes: 0 clean, 1 hits found, 2 usage/config error.",
    )
    p.add_argument(
        "paths",
        nargs="*",
        help="Files or directories. Default: " + ", ".join(DEFAULT_SCAN_PATHS),
    )
    p.add_argument(
        "--phrases",
        help=f"Phrase list (default: ${PHRASES_ENV}, else shared ../{DEFAULT_PHRASES_FILE}, "
        f"else vendored <backend>/{DEFAULT_PHRASES_FILE})",
    )
    p.add_argument("--root", help="BACKEND_ROOT (default: the folder containing tools/)")
    p.add_argument(
        "--check-sync",
        action="store_true",
        help="Only verify that the shared and vendored phrase lists are identical, then exit.",
    )
    p.add_argument(
        "--ext",
        help="Comma-separated extensions for directory scans "
        f"(default: {','.join(DEFAULT_EXTENSIONS)}). Explicit files ignore this.",
    )
    p.add_argument("--include-docs", action="store_true", help="Also descend into docs/ folders.")
    p.add_argument("--json", action="store_true", help="Machine-readable output on stdout.")
    p.add_argument("--list-rules", action="store_true", help="Print parsed rules and exit.")
    return p


def _force_utf8() -> None:
    """Windows consoles default to cp1252; phrases contain non-ASCII characters."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    _force_utf8()
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve() if args.root else backend_root()
    sync_error = check_sync(root)
    if sync_error:
        print(f"lint_forbidden_phrases: {sync_error}", file=sys.stderr)
        return 3
    if args.check_sync:
        print("lint_forbidden_phrases: phrase lists in sync (or only one exists)", file=sys.stderr)
        return 0
    phrases_file = Path(args.phrases) if args.phrases else resolve_phrases_path(root)
    try:
        rules, skipped = load_rules(phrases_file)
    except LintConfigError as exc:
        print(f"lint_forbidden_phrases: {exc}", file=sys.stderr)
        return 2

    if args.list_rules:
        for rule in rules:
            flag = " [needs disclaimer]" if rule.needs_disclaimer else ""
            print(f"{rule.rule_id}{flag}: " + " | ".join(p.text for p in rule.phrases))
        _print_unmechanized(skipped, sys.stdout)
        return 0

    extensions = (
        tuple(e if e.startswith(".") else f".{e}" for e in args.ext.lower().split(","))
        if args.ext
        else DEFAULT_EXTENSIONS
    )
    notes: list[str] = []
    targets: list[Path] = []
    if args.paths:
        for raw in args.paths:
            path = Path(raw)
            if not path.exists():
                print(f"lint_forbidden_phrases: path not found: {raw}", file=sys.stderr)
                return 2
            targets.append(path)
    else:
        for rel in DEFAULT_SCAN_PATHS:
            path = root / rel
            if path.exists():
                targets.append(path)
            else:
                notes.append(f"default path not present yet, skipped: {rel}")

    hits, scanned, scan_notes = scan_paths(
        targets, rules, extensions, args.include_docs, display_root=root
    )
    notes.extend(scan_notes)
    unmech = [asdict(u) for u in skipped] + [
        {"where": "general", "text": text, "why": "known limitation"} for text in STATIC_LIMITATIONS
    ]

    if args.json:
        json.dump(
            {
                "ok": not hits,
                "files_scanned": scanned,
                "hits": [asdict(h) for h in hits],
                "notes": notes,
                "unmechanized": unmech,
            },
            sys.stdout,
            ensure_ascii=False,
            indent=2,
        )
        sys.stdout.write("\n")
    else:
        for h in hits:
            print(
                f'{h.path}:{h.line}:{h.col}: {h.rule_id} forbidden phrase "{h.matched}" '
                f"(rule: {h.phrase!r}; {h.reason}). Use: {h.replacement}"
            )
        for note in notes:
            print(f"note: {note}", file=sys.stderr)
        print(
            f"lint_forbidden_phrases: {scanned} file(s) scanned, {len(hits)} hit(s)",
            file=sys.stderr,
        )
        _print_unmechanized(skipped, sys.stderr)
    return 1 if hits else 0


def _print_unmechanized(skipped: Sequence[Unmechanized], out: TextIO) -> None:
    write = out.write
    write("NOT MECHANIZED (review manually):\n")
    for u in skipped:
        write(f"  - [{u.where}] {u.text} ({u.why})\n")
    for text in STATIC_LIMITATIONS:
        write(f"  - [general] {text}\n")


if __name__ == "__main__":
    raise SystemExit(main())
