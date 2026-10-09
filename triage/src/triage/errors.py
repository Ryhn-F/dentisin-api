"""Typed errors raised by the triage package.

Every error carries a stable, language-neutral ``code`` so that the TypeScript
port can raise the same codes and the shared table (``tests/cases.json``) can
assert on them.
"""

from __future__ import annotations


class TriageError(Exception):
    """Base class for all triage errors."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class TriageInputError(TriageError):
    """The engine input is invalid. The engine never returns a result for it."""


class TriageParamsError(TriageError):
    """The resolved parameter set is invalid or incomplete."""


class RulesValidationError(TriageError):
    """``rules.yaml`` violates ``schema.json`` or a semantic invariant."""
