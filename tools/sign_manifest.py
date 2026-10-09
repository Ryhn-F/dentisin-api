#!/usr/bin/env python3
"""Thin entrypoint for tools/manifest (sign a DentisAI manifest). See tools/manifest/SPEC.md.

Run with the manifest project's environment (it needs `cryptography`):
    uv run --project tools/manifest python tools/sign_manifest.py --help
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "manifest" / "src"


def main() -> int:
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    try:
        cli = importlib.import_module("dentisai_manifest.cli_sign")
    except ModuleNotFoundError as exc:
        print(
            f"error: {exc}. Run: uv run --project tools/manifest python {sys.argv[0]} ...",
            file=sys.stderr,
        )
        return 1
    rc: int = cli.main()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
