"""DentisAI triage: dentist-readable rules + pure reference engine.

Screening only (outputs are indications to be checked by a dentist).
All clinical values are PLACEHOLDER until a dentist signs them off;
``python -m triage.release_check`` blocks releases until then.
"""

from triage.engine import check_params, evaluate, resolve_params
from triage.errors import RulesValidationError, TriageError, TriageInputError, TriageParamsError

__all__ = [
    "RulesValidationError",
    "TriageError",
    "TriageInputError",
    "TriageParamsError",
    "check_params",
    "evaluate",
    "resolve_params",
]
