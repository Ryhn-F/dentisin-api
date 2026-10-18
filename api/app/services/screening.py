"""Screening reference pipeline (stateless; nothing is persisted).

SCREENING, NOT DIAGNOSIS. The clinical logic lives in the Python reference
engines (``triage.evaluate``, ``recommender.recommend``) and their rule files;
this module only orchestrates them and adds the safety envelope every response
carries: ``rules_status``, ``disclaimer_ids``, the referral CTA and versions.

Invariants (tests: tests/test_screening_*.py):
* Red flags win: in ``analyze`` the symptoms are evaluated FIRST; when they alone
  reach the top level, the photo is never fetched (data minimisation) and no
  detector runs. Because the final level is the maximum over all fired rules,
  detections can never lower it.
* Triage input detections are rebuilt as ``{class, confidence}`` only (ADR 014).
  Boxes exist only in the ``overlay`` of ``analyze`` and never reach the engine.
* Errors never yield a (lower) level: invalid input -> typed 422 with the
  engine's code; unavailable stages -> 503. Clients show the referral screen.
* No photo bytes, symptoms or results are logged or stored.
"""

from __future__ import annotations

import base64
import binascii
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

import anyio
from fastapi.exceptions import RequestValidationError
from recommender.recommend import RecommenderError, recommend
from triage.engine import evaluate as triage_evaluate
from triage.errors import TriageInputError

from ..auth import Principal
from ..errors import ApiError
from ..repositories import tables as t
from ..repositories.base import MlRepository
from ..schemas.screening import (
    MAX_DETECTIONS,
    MAX_PHOTO_BYTES,
    AnalyzeRequest,
    EvaluateRequest,
    PhotoScreeningRequest,
    Preferences,
)
from .detector import Detection, Detector, NullDetector
from .quality import QualityGate, QualityOutcome
from .review_queue import eligible_image
from .screening_rules import (
    DISCLAIMER_ID,
    REFERRAL_CTA_ID,
    RULES_NOT_VALIDATED_ID,
    ScreeningRules,
)

ENGINE_TRIAGE: Final = "triage"
ENGINE_RECOMMENDER: Final = "recommender"
FAIL_SAFE_ACTION: Final = "show_referral_screen"


# --------------------------------------------------------------------------- errors
def fail_safe(rules: ScreeningRules | None) -> dict[str, Any]:
    """Instructions attached to every screening error: never show a level, show the
    referral screen instead (the error itself carries no clinical result)."""
    return {
        "action": FAIL_SAFE_ACTION,
        "referral_cta_id": REFERRAL_CTA_ID,
        "disclaimer_ids": [DISCLAIMER_ID],
        "rules_status": rules.rules_status if rules is not None else "draft",
    }


def screening_error(
    rules: ScreeningRules | None,
    status: int,
    code: str,
    message: str,
    *,
    engine: str | None = None,
    stages: list[dict[str, Any]] | None = None,
) -> ApiError:
    extra: dict[str, Any] = {"fail_safe": fail_safe(rules)}
    if engine is not None:
        extra["engine"] = engine
    if stages is not None:
        extra["stages"] = stages
    return ApiError(status, code, message, extra=extra)


def triage_input_error(rules: ScreeningRules | None, exc: TriageInputError) -> ApiError:
    # exc.detail may quote the offending value -> never returned; the code is enough.
    return screening_error(
        rules, 422, exc.code, "Screening input rejected by the triage engine.", engine=ENGINE_TRIAGE
    )


def recommender_input_error(rules: ScreeningRules | None) -> ApiError:
    return screening_error(
        rules,
        422,
        "invalid_input",
        "Preferences rejected by the recommender.",
        engine=ENGINE_RECOMMENDER,
    )


def _engine_check(rules: ScreeningRules, triage_input: object) -> TriageInputError | None:
    try:
        triage_evaluate(rules.triage_rules, triage_input, rules.triage_params)
    except TriageInputError as exc:
        return exc
    return None


_ANALYZE_FIELDS: Final = frozenset(AnalyzeRequest.model_fields)
_PHOTO_FIELDS: Final = frozenset(PhotoScreeningRequest.model_fields)
_ANALYZE_TRIAGE_FIELDS: Final = ("symptoms", "age_months", "pain")


def _loc_root(err: Mapping[str, Any]) -> str | None:
    loc = tuple(err.get("loc", ()))
    return str(loc[1]) if len(loc) > 1 else None


def _api_only_error(
    rules: ScreeningRules, body: Mapping[str, Any], exc: RequestValidationError
) -> ApiError | None:
    """The engine accepted the triage part: blame the API-only fields."""
    roots = {_loc_root(err) for err in exc.errors()}
    if "preferences" in roots:
        return recommender_input_error(rules)
    detections = body.get("detections")
    if isinstance(detections, list) and len(detections) > MAX_DETECTIONS:
        return screening_error(rules, 422, "too_many_detections", "Too many detections.")
    return None


def _rejects_json(exc: RequestValidationError) -> bool:
    return any(err.get("type") == "json_invalid" for err in exc.errors())


def map_evaluate_validation(
    rules: ScreeningRules | None, body: Any, exc: RequestValidationError
) -> ApiError | None:
    """Translate a pydantic rejection of /evaluate into the triage engine's code.

    The engine itself decides: the raw body minus ``preferences`` IS the engine
    input. If the engine accepts it, the problem lies in the API-only parts
    (preferences / transport caps). None -> generic ``validation_error``.
    """
    if rules is None or _rejects_json(exc):
        return None
    if not isinstance(body, Mapping):
        return triage_input_error(rules, TriageInputError("INPUT_NOT_OBJECT", "not an object"))
    engine_error = _engine_check(rules, {k: v for k, v in body.items() if k != "preferences"})
    if engine_error is not None:
        return triage_input_error(rules, engine_error)
    return _api_only_error(rules, body, exc)


def map_analyze_validation(
    rules: ScreeningRules | None, body: Any, exc: RequestValidationError
) -> ApiError | None:
    """Same idea for /analyze: symptoms/age/pain errors carry the engine code."""
    return _map_photo_route_validation(rules, body, exc, _ANALYZE_FIELDS)


def map_photo_validation(
    rules: ScreeningRules | None, body: Any, exc: RequestValidationError
) -> ApiError | None:
    """/photo: symptoms/age/pain errors carry the engine code; a bad `photo` field
    falls through to the generic validation_error (not a triage input)."""
    return _map_photo_route_validation(rules, body, exc, _PHOTO_FIELDS)


def _map_photo_route_validation(
    rules: ScreeningRules | None,
    body: Any,
    exc: RequestValidationError,
    fields: frozenset[str],
) -> ApiError | None:
    if rules is None or _rejects_json(exc):
        return None
    if not isinstance(body, Mapping):
        return triage_input_error(rules, TriageInputError("INPUT_NOT_OBJECT", "not an object"))
    for key in body:
        if key not in fields:
            return triage_input_error(rules, TriageInputError("UNKNOWN_FIELD", "unknown field"))
    for key in _ANALYZE_TRIAGE_FIELDS:
        if key not in body:
            return triage_input_error(rules, TriageInputError("MISSING_FIELD", "missing field"))
    synthetic = {
        "detections": [],
        "photo_status": "not_taken",
        **{key: body[key] for key in _ANALYZE_TRIAGE_FIELDS},
    }
    engine_error = _engine_check(rules, synthetic)
    if engine_error is not None:
        return triage_input_error(rules, engine_error)
    return _api_only_error(rules, body, exc)  # photo_ref / photo -> generic validation_error


# --------------------------------------------------------------------------- envelope
@dataclass
class Envelope:
    triage: dict[str, Any]
    recommendation: dict[str, Any] | None
    recommendation_status: str
    rules_version: dict[str, str]
    rules_status: str
    clinically_validated: bool
    disclaimer_ids: list[str]
    disclaimers: list[dict[str, str]]
    referral: dict[str, Any]


def _run_triage(rules: ScreeningRules, triage_input: Mapping[str, Any]) -> dict[str, Any]:
    try:
        result = triage_evaluate(rules.triage_rules, triage_input, rules.triage_params)
    except TriageInputError as exc:
        raise triage_input_error(rules, exc) from None
    result.pop("rules_version", None)  # reported once, in rules_version.triage
    return result


def _run_recommender(
    rules: ScreeningRules, age_months: int | None, preferences: Preferences | None
) -> tuple[dict[str, Any] | None, str]:
    if age_months is None:
        return None, "skipped_age_unknown"
    valid = rules.recommender_rules["valid_age_months"]
    if not valid["min"] <= age_months <= valid["max"]:
        return None, "skipped_age_out_of_range"
    prefs = preferences or Preferences()
    try:
        result = recommend(
            age_months,
            list(prefs.allergies),
            {"halal": prefs.halal, "no_sls": prefs.no_sls},
            None,  # risk_level: mapping triage level -> risk is a dentist decision (not made)
            rules=rules.recommender_rules,
            products=list(rules.recommender_products),
        )
    except RecommenderError:
        raise recommender_input_error(rules) from None
    return result, "ok"


def build_envelope(
    rules: ScreeningRules,
    triage_result: dict[str, Any],
    recommendation: dict[str, Any] | None,
    recommendation_status: str,
    extra_validated: bool = True,
) -> Envelope:
    approved = rules.rules_status == "approved"
    disclaimer_ids = [DISCLAIMER_ID]
    disclaimers = [{"id": DISCLAIMER_ID, "text": rules.advice_text(DISCLAIMER_ID)}]
    if recommendation is not None:
        for item in recommendation["disclaimers"]:
            if item["id"] not in disclaimer_ids:
                disclaimer_ids.append(str(item["id"]))
                disclaimers.append({"id": str(item["id"]), "text": str(item["text"])})
    if not approved:
        disclaimer_ids.append(RULES_NOT_VALIDATED_ID)
        disclaimers.append({"id": RULES_NOT_VALIDATED_ID, "text": rules.notice})
    return Envelope(
        triage=triage_result,
        recommendation=recommendation,
        recommendation_status=recommendation_status,
        rules_version={"triage": rules.triage_version, "recommender": rules.recommender_version},
        rules_status=rules.rules_status,
        clinically_validated=approved and extra_validated,
        disclaimer_ids=disclaimer_ids,
        disclaimers=disclaimers,
        referral={
            "required": True,
            "cta_id": REFERRAL_CTA_ID,
            "cta_text": rules.advice_text(REFERRAL_CTA_ID),
            "template_id": triage_result["referral_template_id"],
        },
    )


def evaluate(rules: ScreeningRules, body: EvaluateRequest) -> Envelope:
    triage_result = _run_triage(rules, body.triage_input())
    recommendation, rec_status = _run_recommender(rules, body.age_months, body.preferences)
    return build_envelope(rules, triage_result, recommendation, rec_status)


# --------------------------------------------------------------------------- analyze
@dataclass
class StageLog:
    stages: list[dict[str, Any]] = field(default_factory=list)

    def add(self, name: str, status: str, started: float | None = None, **detail: Any) -> None:
        elapsed = 0.0 if started is None else (time.perf_counter() - started) * 1000
        self.stages.append(
            {"name": name, "status": status, "duration_ms": round(elapsed, 2), "detail": detail}
        )

    def skip(self, *names: str) -> None:
        for name in names:
            self.add(name, "skipped")


@dataclass
class AnalyzeResult:
    envelope: Envelope
    outcome: str
    photo_status: str
    stages: list[dict[str, Any]]
    quality: QualityOutcome | None
    detector: Detector | None
    overlay: list[Detection] | None


def _symptom_triage_input(body: AnalyzeRequest | PhotoScreeningRequest) -> dict[str, Any]:
    return {
        "detections": [],
        "photo_status": "not_taken",
        "symptoms": body.symptoms.model_dump(),
        "age_months": body.age_months,
        "pain": body.pain,
    }


def _valid_detections(rules: ScreeningRules, detections: list[Detection]) -> bool:
    classes = {cls["id"] for cls in rules.triage_rules["detection_classes"]}
    return all(d.cls in classes and 0.0 <= d.score <= 1.0 for d in detections)


@dataclass
class _Pipeline:
    rules: ScreeningRules
    body: AnalyzeRequest | PhotoScreeningRequest
    detector: Detector
    quality_gate: QualityGate
    log: StageLog = field(default_factory=StageLog)

    def fail(self, status: int, code: str, message: str) -> ApiError:
        return screening_error(self.rules, status, code, message, stages=self.log.stages)

    def ensure_stages_available(self) -> None:
        """Fail closed BEFORE touching the photo when a stage cannot run."""
        if not self.quality_gate.available:
            self.log.add("quality_gate", "unavailable")
            raise self.fail(503, "quality_gate_unavailable", "Photo quality gate unavailable.")
        if isinstance(self.detector, NullDetector):
            self.log.add("detector", "unavailable", detector=self.detector.name)
            raise self.fail(503, "detector_unavailable", "No detector is configured.")

    async def check_quality(self, photo: bytes) -> QualityOutcome:
        """(c) reference quality gate in a worker thread (CPU-bound)."""
        started = time.perf_counter()
        quality = await anyio.to_thread.run_sync(self.quality_gate.check, photo)
        self.log.add(
            "quality_gate",
            "completed",
            started,
            passed=quality.passed,
            reasons=list(quality.reasons),
            config_status=self.quality_gate.config_status,
        )
        return quality

    async def detect(self, photo: bytes) -> list[Detection]:
        """(d) pluggable detector; any failure or unsupported output fails closed."""
        started = time.perf_counter()
        try:
            detections = await self.detector.detect(photo)
        except Exception as exc:  # any detector failure -> 503, never a result
            self.log.add("detector", "failed", started, code="detector_failed")
            raise self.fail(503, "detector_failed", "The detector failed.") from exc
        if not _valid_detections(self.rules, detections):
            self.log.add("detector", "failed", started, code="detector_output_invalid")
            raise self.fail(
                503, "detector_output_invalid", "The detector returned an unsupported result."
            )
        self.log.add("detector", "completed", started, detections=len(detections))
        return detections

    def finish(
        self,
        triage_input: dict[str, Any],
        outcome: str,
        quality: QualityOutcome | None,
        detections: list[Detection] | None,
        level_source: str,
    ) -> AnalyzeResult:
        """(e) triage + (f) recommendation."""
        started = time.perf_counter()
        triage_result = _run_triage(self.rules, triage_input)
        self.log.add("triage", "completed", started, level_source=level_source)
        started = time.perf_counter()
        recommendation, rec_status = _run_recommender(
            self.rules, self.body.age_months, self.body.preferences
        )
        self.log.add(
            "recommendation",
            "completed" if recommendation else "skipped",
            started,
            recommendation_status=rec_status,
        )
        validated = quality is not None and self.quality_gate.config_status not in (
            "PLACEHOLDER",
            "UNAVAILABLE",
        )
        return AnalyzeResult(
            build_envelope(self.rules, triage_result, recommendation, rec_status, validated),
            outcome,
            str(triage_input["photo_status"]),
            self.log.stages,
            quality,
            self.detector if detections is not None else None,
            detections,
        )


async def _fetch_dataset_photo(
    pipe: _Pipeline, body: AnalyzeRequest, repo: MlRepository, principal: Principal
) -> bytes:
    """(b) consent/flag gating, then the photo bytes (memory only, size-capped)."""
    started = time.perf_counter()
    try:
        image = await eligible_image(repo, str(body.photo_ref))
    except ApiError as exc:
        pipe.log.add("consent", "failed", started, code=exc.code)
        exc.extra = {"fail_safe": fail_safe(pipe.rules), "stages": pipe.log.stages}
        raise
    pipe.log.add("consent", "completed", started)
    started = time.perf_counter()
    try:
        photo = await repo.fetch_photo(image.object_path, MAX_PHOTO_BYTES)
    except ValueError:
        pipe.log.add("photo_fetch", "failed", started, code="photo_not_processable")
        raise pipe.fail(409, "photo_not_processable", "The photo could not be processed.") from None
    pipe.log.add("photo_fetch", "completed", started)
    await repo.audit(
        principal.user_id,
        "screening_analyze_photo_read",
        t.IMAGE_UPLOAD,
        image.id,
        {"detector": pipe.detector.name},
    )
    return photo


async def _run_photo_pipeline(
    pipe: _Pipeline, get_photo: Callable[[], Awaitable[bytes]]
) -> AnalyzeResult:
    """(a) red flags -> stages available -> (b) photo -> (c) quality -> (d) detector -> (e)/(f)."""
    rules = pipe.rules
    symptoms_input = _symptom_triage_input(pipe.body)

    # (a) red flags FIRST, from the questionnaire only. When the symptoms alone reach
    # the top level, nothing a photo shows can change it: skip every photo stage.
    started = time.perf_counter()
    symptoms_level = _run_triage(rules, symptoms_input)["level"]
    short_circuit = symptoms_level == rules.top_level
    pipe.log.add("red_flag", "completed", started, short_circuit=short_circuit)
    if short_circuit:
        pipe.log.skip("consent", "photo_fetch", "quality_gate", "detector")
        return pipe.finish(symptoms_input, "red_flag_short_circuit", None, None, "symptoms_only")

    pipe.ensure_stages_available()
    photo = await get_photo()
    try:
        quality = await pipe.check_quality(photo)
        if not quality.passed:
            pipe.log.skip("detector")
            return pipe.finish(
                {**symptoms_input, "photo_status": "quality_failed"},
                "quality_failed",
                quality,
                None,
                "symptoms_only",
            )
        detections = await pipe.detect(photo)
    finally:
        del photo  # never kept beyond this request

    # (e) triage gets {class, confidence} ONLY; boxes stay in the overlay (ADR 014).
    triage_input = {
        **symptoms_input,
        "photo_status": "assessed",
        "detections": [{"class": d.cls, "confidence": d.score} for d in detections],
    }
    return pipe.finish(triage_input, "completed", quality, detections, "symptoms_and_photo")


async def analyze(
    rules: ScreeningRules,
    body: AnalyzeRequest,
    repo: MlRepository,
    detector: Detector,
    quality_gate: QualityGate,
    principal: Principal,
) -> AnalyzeResult:
    pipe = _Pipeline(rules, body, detector, quality_gate)
    return await _run_photo_pipeline(
        pipe, lambda: _fetch_dataset_photo(pipe, body, repo, principal)
    )


# --------------------------------------------------------------------------- photo
def _decode_photo_field(pipe: _Pipeline, photo_b64: str) -> bytes:
    """(b) the app's photo, base64 -> bytes (memory only, size-capped). A bad photo is a
    transport error, not a triage input: no fail-safe, the app keeps its own result."""
    started = time.perf_counter()
    try:
        photo = base64.b64decode(photo_b64, validate=True)
    except (binascii.Error, ValueError):
        pipe.log.add("photo_fetch", "failed", started, code="photo_invalid")
        raise ApiError(422, "photo_invalid", "The photo is not valid base64.") from None
    if not photo or len(photo) > MAX_PHOTO_BYTES:
        pipe.log.add("photo_fetch", "failed", started, code="photo_too_large")
        raise ApiError(413, "photo_too_large", "The photo is too large.")
    pipe.log.add("photo_fetch", "completed", started)
    return photo


async def screen_photo(
    rules: ScreeningRules,
    body: PhotoScreeningRequest,
    detector: Detector,
    quality_gate: QualityGate,
) -> AnalyzeResult:
    """POST /v1/screening/photo (ADR 020): the same stages as ``analyze``, with the photo
    taken from the request instead of the dataset bucket. Consent (b) is checked by the
    app; nothing is stored (the parent screens their own child, as with /evaluate)."""
    pipe = _Pipeline(rules, body, detector, quality_gate)

    async def get_photo() -> bytes:
        pipe.log.skip("consent")
        return _decode_photo_field(pipe, body.photo)

    return await _run_photo_pipeline(pipe, get_photo)
