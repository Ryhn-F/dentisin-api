"""Reference implementation of the DentisAI toothpaste recommendation (fluoride-only v1).

Design constraints
------------------
* Pure and deterministic: the output depends only on the arguments and on the data in
  ``rules.json`` / ``products.json``. No clock, no randomness, no I/O inside ``recommend``.
* TypeScript-portable: only dicts, lists, strings, integers and booleans; ordering is by
  explicit string/number keys. The app re-implements this function and must pass
  ``tests/cases.json``.
* No clinical thresholds in code: every ppm range, amount and age band lives in
  ``rules.json`` so a dentist can read and change it.
* Screening, not diagnosis: when any rule is not approved the result is flagged
  ``status.needs_review = true`` (the data is still returned) so the UI can show the
  conservative generic message instead.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from functools import cache
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent

_PREF_KEYS = ("halal", "no_sls")


class RecommenderError(ValueError):
    """Base class for typed errors; ``code`` is stable across the Python and TS runtimes."""

    code = "recommender_error"


class InvalidAgeError(RecommenderError):
    code = "invalid_age"


class InvalidInputError(RecommenderError):
    code = "invalid_input"


class RulesError(RecommenderError):
    code = "rules_error"


@cache
def load_rules() -> dict[str, Any]:
    """Load ``rules.json``. The result is shared (cached): callers must not mutate it."""
    data: dict[str, Any] = json.loads((_HERE / "rules.json").read_text(encoding="utf-8"))
    return data


@cache
def load_products() -> dict[str, Any]:
    """Load ``products.json``. The result is shared (cached): callers must not mutate it."""
    data: dict[str, Any] = json.loads((_HERE / "products.json").read_text(encoding="utf-8"))
    return data


def _check_age(age_months: object, valid: Mapping[str, Any]) -> int:
    if isinstance(age_months, bool) or not isinstance(age_months, int):
        raise InvalidAgeError("age_months must be a whole number of months")
    if age_months < valid["min"] or age_months > valid["max"]:
        raise InvalidAgeError(
            f"age_months must be between {valid['min']} and {valid['max']}, got {age_months}"
        )
    return age_months


def _normalize_allergies(allergies: object) -> list[str]:
    if isinstance(allergies, str) or not isinstance(allergies, Sequence):
        raise InvalidInputError("allergies must be a list of allergen codes")
    codes: set[str] = set()
    for item in allergies:
        if not isinstance(item, str):
            raise InvalidInputError("allergies must contain only strings")
        code = item.strip().lower()
        if code:
            codes.add(code)
    return sorted(codes)


def _normalize_prefs(prefs: object) -> dict[str, bool]:
    result = dict.fromkeys(_PREF_KEYS, False)
    if prefs is None:
        return result
    if not isinstance(prefs, Mapping):
        raise InvalidInputError("prefs must be an object")
    for key, value in prefs.items():
        if key not in result:
            raise InvalidInputError(f"unknown preference: {key}")
        if not isinstance(value, bool):
            raise InvalidInputError(f"preference {key} must be true or false")
        result[key] = value
    return result


def _find_band(bands: Sequence[Mapping[str, Any]], age_months: int) -> Mapping[str, Any]:
    for band in bands:
        span = band["age_months"]
        if span["min"] <= age_months <= span["max"]:
            return band
    raise RulesError(f"no age band covers {age_months} months")


def _passes_filters(
    product: Mapping[str, Any], allergies: Sequence[str], prefs: Mapping[str, bool]
) -> bool:
    if allergies:
        listed = product["allergens"]
        # Unknown allergens (None) cannot be confirmed safe, so the product is dropped.
        if listed is None or any(code in listed for code in allergies):
            return False
    if prefs["halal"] and product["halal_certified"] is not True:
        return False
    return not (prefs["no_sls"] and product["contains_sls"] is not False)


def _add_review_reason(reasons: set[str], path: str, meta: Mapping[str, Any]) -> None:
    if meta["status"] != "approved":
        reasons.add(f"rules:{path}:{meta['status']}")


def _unique(items: Sequence[str]) -> list[str]:
    seen: dict[str, None] = {}
    for item in items:
        seen.setdefault(item, None)
    return list(seen)


def recommend(
    age_months: int,
    allergies: Sequence[str] = (),
    prefs: Mapping[str, bool] | None = None,
    risk_level: str | None = None,
    *,
    rules: Mapping[str, Any] | None = None,
    products: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Recommend fluoride toothpaste guidance for a child.

    ``rules`` and ``products`` default to the shipped files; they can be injected for tests
    and for the TS runtime. Raises ``InvalidAgeError`` / ``InvalidInputError`` on bad input.
    """
    r = rules if rules is not None else load_rules()
    catalog = products if products is not None else load_products()["products"]
    messages: Mapping[str, Mapping[str, Any]] = r["messages"]

    age = _check_age(age_months, r["valid_age_months"])
    allergy_codes = _normalize_allergies(allergies)
    pref = _normalize_prefs(prefs)
    if risk_level is not None and risk_level not in r["risk_levels"]:
        raise InvalidInputError(f"unknown risk_level: {risk_level}")

    band = _find_band(r["age_bands"], age)
    band_id: str = band["id"]
    reasons: set[str] = set()
    _add_review_reason(reasons, "valid_age_months", r["valid_age_months"])
    _add_review_reason(reasons, "regulatory_ceiling_ppm", r["regulatory_ceiling_ppm"])
    for attr in ("age_months", "fluoride_ppm", "amount", "supervision"):
        _add_review_reason(reasons, f"{band_id}.{attr}", band[attr])

    # --- allergy / preference filters (FR-52) ---------------------------------------------
    known_allergens = {item["code"] for item in r["filters"]["allergen_codes"]}
    unknown_allergies = [code for code in allergy_codes if code not in known_allergens]
    for code in allergy_codes:
        implied = r["filters"]["allergy_implies_pref"].get(code)
        if implied is not None:
            pref[implied] = True
    for code in unknown_allergies:
        reasons.add(f"unknown_allergy:{code}")
    if allergy_codes or any(pref.values()):
        _add_review_reason(reasons, "filters", r["filters"])

    # --- fluoride ppm options ---------------------------------------------------------------
    ppm_block = band["fluoride_ppm"]
    selected = ppm_block["selected_candidate_id"]
    ceiling: int = r["regulatory_ceiling_ppm"]["value"]
    ppm_options: list[dict[str, Any]] = []
    for cand in ppm_block["candidates"]:
        if selected is not None and cand["id"] != selected:
            continue
        upper = min(cand["ppm_max"], ceiling)
        if cand["ppm_min"] > upper:
            reasons.add(f"ppm_candidate_above_ceiling:{cand['id']}")
            continue
        if upper != cand["ppm_max"]:
            reasons.add("ppm_ceiling_applied")
        ppm_options.append(
            {
                "candidate_id": cand["id"],
                "label": cand["label"],
                "min": cand["ppm_min"],
                "max": upper,
                "source": cand["source"],
            }
        )
    if ppm_block["decision_pending"]:
        reasons.add("ppm_decision_pending")

    # --- example products (FR-51), content first -------------------------------------------
    matched: list[dict[str, Any]] = []
    for product in sorted(catalog, key=lambda item: str(item["id"])):
        hits = [o["candidate_id"] for o in ppm_options if o["min"] <= product["ppm"] <= o["max"]]
        if hits and _passes_filters(product, allergy_codes, pref):
            matched.append(
                {
                    "id": product["id"],
                    "brand": product["brand"],
                    "ppm": product["ppm"],
                    "bpom_notification_no": product["bpom_notification_no"],
                    "verified": product["verified"],
                    "verified_at": product["verified_at"],
                    "synthetic": product["synthetic"],
                    "matches_candidate_ids": hits,
                }
            )
    if any(not item["verified"] for item in matched):
        reasons.add("products_unverified")

    note_ids = r["product_note_ids"]
    product_note_ids: list[str] = []
    if matched:
        product_note_ids.append(note_ids["examples"])
    if ppm_options and len(matched) < r["min_products"]:
        product_note_ids.append(note_ids["fewer_than_two"])
    if unknown_allergies:
        product_note_ids.append(note_ids["allergy_unverified"])

    # --- user-facing text ----------------------------------------------------------------------
    supervision = band["supervision"]
    fluoride_note_ids = _unique(
        [*band["note_ids"], *(r["risk_note_ids"][risk_level] if risk_level is not None else [])]
    )
    disclaimer_ids = list(r["mandatory_disclaimer_ids"])
    used_message_ids = [
        *disclaimer_ids,
        *fluoride_note_ids,
        *product_note_ids,
        band["amount"]["message_id"],
        *([supervision["message_id"]] if supervision["message_id"] is not None else []),
    ]
    for message_id in _unique(used_message_ids):
        _add_review_reason(reasons, f"message:{message_id}", messages[message_id])

    def text(message_id: str) -> str:
        return str(messages[message_id]["text"])

    needs_review = bool(reasons)
    generic_id: str = band["generic_message_id"]
    return {
        "fluoride": {
            "band_id": band_id,
            "ppm_options": ppm_options,
            "decision_pending": bool(ppm_block["decision_pending"]),
            "amount": {
                "code": band["amount"]["code"],
                "text": text(band["amount"]["message_id"]),
            },
            "supervision": {
                "required": supervision["required"],
                "until_age_months": supervision["until_age_months"],
                "text": (
                    text(supervision["message_id"])
                    if supervision["message_id"] is not None
                    else None
                ),
            },
            "notes": [{"id": i, "text": text(i)} for i in fluoride_note_ids],
        },
        "disclaimer_ids": disclaimer_ids,
        "disclaimers": [{"id": i, "text": text(i)} for i in disclaimer_ids],
        "products": matched,
        "notes": [{"id": i, "text": text(i)} for i in product_note_ids],
        "status": {
            "needs_review": needs_review,
            "reasons": sorted(reasons),
            "generic_message": {"id": generic_id, "text": text(generic_id)}
            if needs_review
            else None,
        },
        "rules_version": r["rules_version"],
    }
