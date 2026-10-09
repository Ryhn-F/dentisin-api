from __future__ import annotations

import pytest

from rules_loader import Rules, load_rules


@pytest.fixture
def rules() -> Rules:
    """Default rules from brushing_rules.yaml (placeholders included)."""
    return load_rules()


@pytest.fixture
def short_rules() -> Rules:
    """Same rules with a 10 s timer so tests stay small."""
    return load_rules(overrides={"target_duration_s": 10, "min_sextant_ms": 3000})
