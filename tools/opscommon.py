"""Shared helpers for the DentisAI ops tools (stdlib only).

Nothing in here ever prints a secret value. Functions that report problems name
the environment variable, never its content.
"""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Iterable, Mapping, MutableMapping
from pathlib import Path


class ConfigError(Exception):
    """Raised when required configuration is missing or unsafe."""


def backend_root() -> Path:
    """Return BACKEND_ROOT: the folder that contains ``tools/`` (this file lives in tools/).

    The backend folder is self-contained and may be a repository of its own or a
    sub-folder of a monorepo; nothing here assumes a parent directory exists.
    """
    return Path(__file__).resolve().parent.parent


repo_root = backend_root  # backwards-compatible alias


def protected_roots() -> list[Path]:
    """Folders that must never receive backups/exports (they may end up in git).

    BACKEND_ROOT itself, plus the enclosing git work tree or monorepo root if there is one.
    """
    root = backend_root()
    roots = [root]
    for ancestor in root.parents:
        if (ancestor / ".git").exists():
            roots.append(ancestor)
            break
    parent = root.parent
    if (parent / "CLAUDE.md").is_file() and parent not in roots:
        roots.append(parent)
    return roots


def is_inside(path: Path, root: Path) -> bool:
    """True if ``path`` equals ``root`` or lies below it (symlinks and case resolved)."""
    p = Path(os.path.normcase(str(path.resolve())))
    r = Path(os.path.normcase(str(root.resolve())))
    return p == r or r in p.parents


def is_inside_any(path: Path, roots: Iterable[Path]) -> bool:
    return any(is_inside(path, r) for r in roots)


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].lstrip()
    key, _, value = stripped.partition("=")
    key = key.strip()
    if not key.replace("_", "").isalnum():
        return None
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    elif " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    return key, value


def load_env_file(path: Path, environ: MutableMapping[str, str]) -> int:
    """Load KEY=VALUE pairs from ``path`` into ``environ`` without overriding set values.

    Empty values are ignored. Returns the number of variables that were set.
    """
    if not path.is_file():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parsed = _parse_env_line(line)
        if parsed is None:
            continue
        key, value = parsed
        if value and not environ.get(key):
            environ[key] = value
            count += 1
    return count


def get_env(name: str, environ: Mapping[str, str]) -> str | None:
    """Return the stripped value of ``name`` or None when unset/empty."""
    value = environ.get(name, "").strip()
    return value or None


def require_env(names: Iterable[str], environ: Mapping[str, str]) -> dict[str, str]:
    """Return values for all ``names`` or raise ConfigError naming every missing one."""
    values: dict[str, str] = {}
    missing: list[str] = []
    for name in names:
        value = get_env(name, environ)
        if value is None:
            missing.append(name)
        else:
            values[name] = value
    if missing:
        raise ConfigError(
            "Missing required environment variable(s): "
            + ", ".join(missing)
            + ". Set them in your shell or in the root .env (never commit it)."
        )
    return values


def _jwt_role(token: str) -> str | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except (ValueError, UnicodeDecodeError):
        return None
    role = data.get("role") if isinstance(data, dict) else None
    return role if isinstance(role, str) else None


def looks_like_service_role(key: str) -> bool:
    """Heuristic: True if a Supabase key is a privileged (service_role / secret) key."""
    key = key.strip()
    if key.startswith("sb_secret_"):
        return True
    return _jwt_role(key) == "service_role"
