"""docs/API.md examples must be what the code actually produces."""

from __future__ import annotations


def test_api_md_examples_are_fresh() -> None:
    from scripts.gen_api_examples import main

    assert main(["--check"]) == 0, "run: uv run python scripts/gen_api_examples.py"


def test_api_md_render_rejects_unknown_or_missing_markers() -> None:
    import pytest
    from scripts.gen_api_examples import render

    doc = "<!-- example:known -->\n```json\n{}\n```"
    assert '"x": 1' in render(doc, {"known": {"x": 1}})
    with pytest.raises(SystemExit):
        render(doc, {"other": {}})
    with pytest.raises(SystemExit):
        render(doc + "\n<!-- example:extra -->\n```json\n{}\n```", {"known": {}})
