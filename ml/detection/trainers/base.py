"""Shared types for trainer adapters."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any


class MissingOptionalDependencyError(RuntimeError):
    """An optional heavy dependency is not installed. The message says how to install it."""


@dataclass(frozen=True)
class TrainContext:
    cfg: dict[str, Any]
    run_dir: Path
    canonical: Path
    splits: Path
    images_root: Path | None
    seed: int


def require(module: str, install_hint: str) -> ModuleType:
    """Import ``module`` or raise :class:`MissingOptionalDependencyError` with ``install_hint``."""
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise MissingOptionalDependencyError(
            f"optional dependency {module!r} is not installed ({exc}). Install with: {install_hint}"
        ) from exc
