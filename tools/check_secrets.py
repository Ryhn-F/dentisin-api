#!/usr/bin/env python3
"""Fail if secret-looking files or strings are tracked in the repository (stdlib only).

Used by the ``secrets-scan`` CI job. Inside a git checkout it inspects ``git ls-files``;
otherwise it walks the directory (skipping .git, .venv, node_modules).

Checks
1. File names that must never be tracked: ``.env`` / ``.env.*`` (except ``.env.example``),
   ``*.pem``, ``*.key``, ``*.age``, ``*.sql.age``, ``*.dump``, ``manifest_signing*``,
   anything under ``dataset_export/``.
2. Content patterns: age private keys, PEM private key blocks, Supabase ``sb_secret_`` keys,
   and JWTs whose payload says ``role: service_role``.

Findings are reported as ``path:line: rule`` and NEVER echo the matched value.
Exit codes: 0 clean, 1 findings, 2 error.
"""

from __future__ import annotations

import argparse
import base64
import fnmatch
import json
import re
import subprocess
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

FORBIDDEN_NAME_GLOBS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.age",
    "*.dump",
    "manifest_signing*",
)
ALLOWED_NAMES = frozenset({".env.example"})
FORBIDDEN_DIRS = ("dataset_export",)
SKIP_DIRS = frozenset(
    {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
)
MAX_BYTES = 2_000_000

CONTENT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("age-private-key", re.compile(r"AGE-SECRET-KEY-1[0-9A-Z]{20,}")),
    ("pem-private-key", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")),
    ("supabase-secret-key", re.compile(r"sb_secret_[A-Za-z0-9_\-]{10,}")),
)
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.(eyJ[A-Za-z0-9_\-]{8,})\.[A-Za-z0-9_\-]{8,}")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int  # 0 for file-name findings
    rule: str


def _name_finding(rel: str) -> str | None:
    posix = PurePosixPath(rel)
    if posix.name in ALLOWED_NAMES:
        return None
    if any(part in FORBIDDEN_DIRS for part in posix.parts[:-1]):
        return "forbidden-directory"
    for pattern in FORBIDDEN_NAME_GLOBS:
        if fnmatch.fnmatch(posix.name, pattern):
            return f"forbidden-file-name({pattern})"
    return None


def _jwt_is_service_role(payload_b64: str) -> bool:
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, UnicodeDecodeError):
        return False
    return isinstance(data, dict) and data.get("role") == "service_role"


def scan_text(text: str, rel: str) -> list[Finding]:
    findings: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for rule, pattern in CONTENT_RULES:
            if pattern.search(line):
                findings.append(Finding(rel, lineno, rule))
        for m in _JWT_RE.finditer(line):
            if _jwt_is_service_role(m.group(1)):
                findings.append(Finding(rel, lineno, "service-role-jwt"))
    return findings


def _gitignore_patterns(root: Path) -> tuple[list[str], list[str]]:
    """Very small .gitignore reader for the non-git fallback: (ignore, negate) globs."""
    ignore: list[str] = []
    negate: list[str] = []
    gitignore = root / ".gitignore"
    if gitignore.is_file():
        for raw in gitignore.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            (negate if line.startswith("!") else ignore).append(line.lstrip("!").strip("/"))
    return ignore, negate


def _ignored(rel: str, ignore: list[str], negate: list[str]) -> bool:
    name = PurePosixPath(rel).name
    if any(fnmatch.fnmatch(name, p) or fnmatch.fnmatch(rel, p) for p in negate):
        return False
    return any(
        fnmatch.fnmatch(name, p) or fnmatch.fnmatch(rel, p) or rel.startswith(p + "/")
        for p in ignore
    )


def list_files(root: Path) -> Iterator[str]:
    """Yield repo-relative POSIX paths: tracked files if git is usable, else a directory walk
    that honours a simple .gitignore (so a developer's own untracked .env is not reported)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            check=True,
            capture_output=True,
        ).stdout
        for item in out.split(b"\0"):
            if item:
                yield item.decode("utf-8", errors="replace")
        return
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    ignore, negate = _gitignore_patterns(root)
    stack = [root]
    while stack:
        current = stack.pop()
        for entry in sorted(current.iterdir()):
            rel = entry.relative_to(root).as_posix()
            if entry.is_dir():
                if entry.name not in SKIP_DIRS and not _ignored(rel, ignore, negate):
                    stack.append(entry)
            elif not _ignored(rel, ignore, negate):
                yield rel


def scan_repo(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for rel in list_files(root):
        reason = _name_finding(rel)
        if reason:
            findings.append(Finding(rel, 0, reason))
            continue
        path = root / rel
        try:
            if not path.is_file() or path.stat().st_size > MAX_BYTES:
                continue
            text = path.read_bytes().decode("utf-8", errors="ignore")
        except OSError:
            continue
        findings.extend(scan_text(text, rel))
    return findings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0] if __doc__ else "")
    parser.add_argument("--root", default=".", help="Repository root (default: cwd)")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"check_secrets: not a directory: {args.root}", file=sys.stderr)
        return 2
    findings = scan_repo(root)
    for f in findings:
        where = f.path if f.line == 0 else f"{f.path}:{f.line}"
        print(f"{where}: {f.rule}")
    if findings:
        print(
            f"check_secrets: {len(findings)} finding(s). Values are never printed. "
            "Remove the file/string, rotate the secret, and purge it from history.",
            file=sys.stderr,
        )
        return 1
    print("check_secrets: clean", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
