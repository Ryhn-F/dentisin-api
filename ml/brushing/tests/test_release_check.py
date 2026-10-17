"""Release gate for brushing_rules.yaml: blocked today; dentist or literature basis (ADR 015)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from release_check import collect_blockers, main, review_basis, reviewer_ok
from rules_loader import DEFAULT_RULES_PATH, rules_from_mapping

TEAM_REVIEWER = "SYNTHETIC_TEAM_MEMBER"  # tests only, not a real person


def _raw() -> dict[str, Any]:
    with DEFAULT_RULES_PATH.open(encoding="utf-8") as fh:
        data: dict[str, Any] = yaml.safe_load(fh)
    return data


def _released(basis: str | None) -> dict[str, Any]:
    """Synthetic releasable copy: no placeholders, reviewed, no PERLU VERIFIKASI markers."""
    data = copy.deepcopy(_raw())
    if basis is not None:
        data["review_basis"] = basis
    data["status"] = "FIXED_BY_GUIDELINE"
    data["reviewed_by"] = TEAM_REVIEWER if basis == "literature" else "SYNTHETIC_TEST_REVIEWER"
    data["reviewed_at"] = "2000-01-01"
    for entry in data["params"].values():
        entry["status"] = "FIXED_BY_GUIDELINE"
        entry["rationale"] = entry["rationale"].replace("[PERLU VERIFIKASI]", "").strip()
        if basis == "literature":
            entry["source"] = "SYNTHETIC_CITATION (tests only)"
    return data


def test_shipped_rules_are_blocked(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 1
    out = capsys.readouterr().out
    assert "review_basis: dentist" in out  # the field is absent from the shipped file
    assert "rules status is 'PLACEHOLDER'" in out
    assert "param 'min_motion_variance': status PLACEHOLDER" in out
    assert "not reviewed by a dentist (reviewed_by)" in out
    assert "reviewed_at missing" in out
    assert "param 'target_duration_s': rationale still marked PERLU VERIFIKASI" in out


def test_shipped_rules_have_no_review_basis_yet() -> None:
    assert "review_basis" not in _raw()
    assert review_basis(_raw()) == "dentist"


@pytest.mark.parametrize("basis", [None, "dentist", "literature"])
def test_releasable_copy_passes(basis: str | None) -> None:
    assert collect_blockers(_released(basis)) == []


def test_absent_basis_equals_explicit_dentist() -> None:
    for data in (_raw(), _released(None)):
        assert collect_blockers({**data, "review_basis": "dentist"}) == collect_blockers(data)


@pytest.mark.parametrize("reviewer", [None, "PENDING_DRG"])
def test_dentist_basis_needs_a_dentist(reviewer: str | None) -> None:
    for basis in (None, "dentist"):
        data = _released(basis)
        data["reviewed_by"] = reviewer
        assert collect_blockers(data) == ["not reviewed by a dentist (reviewed_by)"]


def test_dentist_basis_does_not_require_sources() -> None:
    data = _released("dentist")
    assert all("source" not in entry for entry in data["params"].values())
    assert collect_blockers(data) == []


@pytest.mark.parametrize("reviewer", [None, "", "   ", "PENDING_DRG", "PLACEHOLDER", "null"])
def test_literature_basis_rejects_placeholder_reviewers(reviewer: str | None) -> None:
    data = _released("literature")
    data["reviewed_by"] = reviewer
    assert collect_blockers(data) == ["reviewed_by is not a named team reviewer (literature basis)"]


def test_literature_basis_requires_a_source_per_param() -> None:
    data = _released("literature")
    del data["params"]["window_ms"]["source"]
    data["params"]["min_face_conf"]["source"] = "  "
    data["reviewed_at"] = None
    assert collect_blockers(data) == [
        "reviewed_at missing",
        "param 'min_face_conf': source missing (literature basis needs a citation)",
        "param 'window_ms': source missing (literature basis needs a citation)",
    ]


def test_literature_basis_rejects_perlu_verifikasi() -> None:
    data = _released("literature")
    data["params"]["window_ms"]["source"] = "Pedoman X [PERLU VERIFIKASI]"
    assert collect_blockers(data) == ["param 'window_ms': source still marked PERLU VERIFIKASI"]


def test_literature_basis_keeps_every_other_check() -> None:
    draft = {**_raw(), "review_basis": "literature"}
    text = "\n".join(collect_blockers(draft))
    assert "rules status is 'PLACEHOLDER'" in text
    assert "param 'windows': status PLACEHOLDER" in text
    assert "reviewed_by is not a named team reviewer" in text
    assert "reviewed_at missing" in text
    assert "rationale still marked PERLU VERIFIKASI" in text


def test_reviewer_ok_per_basis() -> None:
    assert reviewer_ok("Tim A", "literature")
    assert reviewer_ok("PLACEHOLDER", "dentist")  # dentist basis: only null/PENDING_DRG fail
    assert not reviewer_ok("tbd", "literature")
    assert not reviewer_ok(None, "dentist")


def test_invalid_review_basis_is_rejected_by_the_loader() -> None:
    with pytest.raises(ValueError, match="review_basis"):
        rules_from_mapping({**_raw(), "review_basis": "team"})


def test_main_passes_and_reports_invalid_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(_released("literature")), encoding="utf-8")
    assert main(["--rules", str(path)]) == 0
    assert "Release check passed." in capsys.readouterr().out

    path.write_text(yaml.safe_dump({**_raw(), "review_basis": "team"}), encoding="utf-8")
    assert main(["--rules", str(path)]) == 1
    assert "rules file invalid: review_basis must be one of" in capsys.readouterr().out

    path.write_text("- a\n", encoding="utf-8")
    assert main(["--rules", str(path)]) == 1
    assert "must contain a mapping" in capsys.readouterr().out
