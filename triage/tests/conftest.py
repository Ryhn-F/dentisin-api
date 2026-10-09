"""Shared fixtures. Test-only params come from tests/fixtures/params_profiles.json."""

from __future__ import annotations

from typing import Any

import pytest

from triage.forbidden import ForbiddenList, load_forbidden
from triage.loader import load_rules, read_json
from triage.paths import CASES_PATH, PROFILES_PATH


@pytest.fixture(scope="session")
def rules() -> dict[str, Any]:
    return load_rules()


@pytest.fixture(scope="session")
def profiles() -> dict[str, Any]:
    data: dict[str, Any] = read_json(PROFILES_PATH)["profiles"]
    return data


@pytest.fixture(scope="session")
def cases_doc() -> dict[str, Any]:
    data: dict[str, Any] = read_json(CASES_PATH)
    return data


@pytest.fixture(scope="session")
def forbidden() -> ForbiddenList:
    return load_forbidden()


def engine_input(case_input: dict[str, Any]) -> dict[str, Any]:
    """Case input without the test-harness-only ``params_profile`` key."""
    return {key: value for key, value in case_input.items() if key != "params_profile"}
