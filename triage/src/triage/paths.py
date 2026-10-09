"""Well-known file locations (I/O helpers only; the engine never imports this).

Layout: ``<BACKEND_ROOT>/triage/`` is this project; ``BACKEND_ROOT`` is the
independent backend folder (``backend/``).
"""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent.parent  # backend/triage/
BACKEND_ROOT = PROJECT_DIR.parent  # backend/

RULES_PATH = PROJECT_DIR / "rules.yaml"
SCHEMA_PATH = PROJECT_DIR / "schema.json"
DIST_DIR = PROJECT_DIR / "dist"
TESTS_DIR = PROJECT_DIR / "tests"
CASES_PATH = TESTS_DIR / "cases.json"
CASES_SCHEMA_PATH = PROJECT_DIR / "schemas" / "cases.schema.json"
PROFILES_PATH = TESTS_DIR / "fixtures" / "params_profiles.json"

FORBIDDEN_PHRASES_ENV = "FORBIDDEN_PHRASES_PATH"
# Shared original (product repo) and the vendored copy inside the backend folder.
SHARED_FORBIDDEN_PHRASES_PATH = BACKEND_ROOT.parent / "docs" / "style" / "forbidden_phrases.md"
VENDORED_FORBIDDEN_PHRASES_PATH = BACKEND_ROOT / "docs" / "style" / "forbidden_phrases.md"


def forbidden_phrases_path() -> Path:
    """Env ``FORBIDDEN_PHRASES_PATH`` > shared original (if present) > vendored copy."""
    override = os.environ.get(FORBIDDEN_PHRASES_ENV)
    if override:
        return Path(override)
    if SHARED_FORBIDDEN_PHRASES_PATH.is_file():
        return SHARED_FORBIDDEN_PHRASES_PATH
    return VENDORED_FORBIDDEN_PHRASES_PATH
