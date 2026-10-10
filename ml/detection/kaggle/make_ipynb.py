"""Generate Kaggle/Colab ``.ipynb`` files from the reviewable percent-format sources (``*_kaggle.py``).

    python kaggle/make_ipynb.py            # (re)write kaggle/*.ipynb
    python kaggle/make_ipynb.py --check    # exit 1 if any .ipynb is missing/out of date (used by tests)

The ``.py`` file is the source of truth (review, ruff); the ``.ipynb`` is a deterministic build
product (no outputs, no execution counts) so it can be uploaded to Kaggle ("File -> Import notebook").
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SOURCES = ("yolox_nano_kaggle.py", "mediapipe_ssd_kaggle.py")


def split_cells(text: str) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    kind: str | None = None
    title = ""
    buf: list[str] = []

    def flush() -> None:
        if kind is None:
            return
        lines = list(buf)
        while lines and not lines[-1].strip():
            lines.pop()
        if kind == "markdown":
            src = [ln[2:] if ln.startswith("# ") else ln.lstrip("#") for ln in lines]
            cells.append({"cell_type": "markdown", "metadata": {}, "source": _join(src)})
        else:
            src = ([f"# {title}"] if title else []) + lines
            cells.append({"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": _join(src)})

    for line in text.splitlines():
        if line.startswith("# %%"):
            flush()
            rest = line[4:].strip()
            kind, title, buf = ("markdown", "", []) if rest.startswith("[markdown]") else ("code", rest, [])
        elif kind is not None:
            buf.append(line)
    flush()
    return cells


def _join(lines: list[str]) -> list[str]:
    return [ln + "\n" for ln in lines[:-1]] + lines[-1:] if lines else []


def to_notebook(text: str) -> dict[str, Any]:
    return {
        "cells": split_cells(text),
        "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                     "language_info": {"name": "python"}, "accelerator": "GPU"},
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def render(src: Path) -> str:
    return json.dumps(to_notebook(src.read_text(encoding="utf-8")), indent=1, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--check", action="store_true")
    p.add_argument("--dir", type=Path, default=HERE)
    args = p.parse_args(argv)
    stale = []
    for name in SOURCES:
        src = args.dir / name
        dst = src.with_suffix(".ipynb")
        text = render(src)
        if args.check:
            if not dst.is_file() or dst.read_text(encoding="utf-8") != text:
                stale.append(dst.name)
        else:
            dst.write_text(text, encoding="utf-8", newline="\n")
            print("wrote", dst)
    if stale:
        print("out of date (run python kaggle/make_ipynb.py):", ", ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
