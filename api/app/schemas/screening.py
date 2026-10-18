"""Screening request/response models (``/v1/screening``).

REQUEST models mirror ``triage/dist/input.schema.json`` EXACTLY (types, enums,
ranges, required + nullable fields, no additional properties).
``tests/test_screening_schema_drift.py`` fails as soon as the generated JSON
schema and these models disagree. Number semantics follow the engine (JS parity):
booleans and strings are never numbers; an integral float such as ``96.0`` is an
integer (JSON Schema 2020-12 agrees).

Detections are ``{class, confidence}`` only (ADR 014): any other key, e.g.
``box``, is rejected (engine code ``DETECTION_INVALID``). Box geometry is never a
triage input; ``/analyze`` returns boxes ONLY in the separate ``overlay`` field.
"""

from __future__ import annotations

import math
from typing import Annotated, Any, Final, Literal
from uuid import UUID

from pydantic import BeforeValidator, ConfigDict, Field

from .common import ResponseModel, StrictModel

# --- API transport limits (NOT part of the triage contract; see docs/API.md) --------------
MAX_DETECTIONS: Final = 100
MAX_ALLERGIES: Final = 10
MAX_ALLERGY_CODE_CHARS: Final = 64
# /photo: base64 of a JPEG/PNG <= 512 000 bytes (same cap as the dataset_photos bucket).
MAX_PHOTO_BYTES: Final = 512_000
MAX_PHOTO_BASE64_CHARS: Final = 4 * ((MAX_PHOTO_BYTES + 2) // 3)

Answer = Literal["yes", "no", "unknown"]
CariesClass = Literal["caries_permanent", "caries_primary"]
PhotoStatus = Literal["assessed", "not_taken", "quality_failed"]
Level = Literal["emergency", "urgent", "scheduled", "routine"]
RulesStatusField = Literal["draft", "needs_review", "approved"]


def _engine_integer(value: object) -> object:
    """JSON integer with the engine's semantics: bool/str rejected, 96.0 -> 96."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("must be an integer")
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError("must be an integer")
        return int(value)
    return value


EngineInt = BeforeValidator(_engine_integer)
AgeMonths = Annotated[int, Field(ge=0, le=300, strict=True), EngineInt]
PainScore = Annotated[int, Field(ge=0, le=10, strict=True), EngineInt]
Confidence = Annotated[float, Field(ge=0, le=1, strict=True, allow_inf_nan=False)]


class Symptoms(StrictModel):
    """Every question of triage/rules.yaml must be answered: "unknown" is a valid
    answer and is NEVER treated as "no" by the engine."""

    airway_or_swallowing: Answer
    lethargy: Answer
    trismus: Answer
    facial_swelling: Answer
    fever_with_toothache: Answer
    pus_or_fistula: Answer
    dental_trauma: Answer
    night_pain: Answer
    white_spot: Answer


class DetectionIn(StrictModel):
    """One model finding: class + confidence ONLY. No geometry (ADR 014)."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=False)

    class_: CariesClass = Field(alias="class")
    confidence: Confidence


class Preferences(StrictModel):
    """Toothpaste filters (recommender FR-52). Unknown allergy codes are kept and
    reported (``unknown_allergy:<code>``), never silently dropped."""

    allergies: list[Annotated[str, Field(min_length=1, max_length=MAX_ALLERGY_CODE_CHARS)]] = Field(
        default_factory=list, max_length=MAX_ALLERGIES
    )
    halal: bool = Field(default=False, strict=True)
    no_sls: bool = Field(default=False, strict=True)


class EvaluateRequest(StrictModel):
    """Device-provided screening inputs = the triage engine input + preferences."""

    detections: list[DetectionIn] = Field(
        max_length=MAX_DETECTIONS,
        description="On-device model findings, {class, confidence} only. Empty list when "
        "no photo was assessed. Boxes are rejected (ADR 014).",
    )
    photo_status: PhotoStatus = Field(
        description="assessed = photo passed the on-device quality gate and was run through "
        "the model; not_taken / quality_failed otherwise (detections must then be empty)."
    )
    symptoms: Symptoms
    age_months: AgeMonths | None = Field(description="Age in months, or null if unknown.")
    pain: PainScore | Literal["unknown"] = Field(description="Pain score 0-10 or 'unknown'.")
    preferences: Preferences | None = Field(
        default=None, description="Optional toothpaste filters (not part of the triage input)."
    )

    def triage_input(self) -> dict[str, Any]:
        """The engine input, rebuilt field by field (no geometry can leak through)."""
        return {
            "detections": [
                {"class": det.class_, "confidence": det.confidence} for det in self.detections
            ],
            "photo_status": self.photo_status,
            "symptoms": self.symptoms.model_dump(),
            "age_months": self.age_months,
            "pain": self.pain,
        }


class AnalyzeRequest(StrictModel):
    """QA/second-opinion pipeline on a CONSENTED dataset photo, by reference only."""

    photo_ref: UUID = Field(
        description="public.image_upload.id of a consented dataset photo (consent (c) active, "
        "dataset_contribution ON). Raw photos are never accepted."
    )
    symptoms: Symptoms
    age_months: AgeMonths | None
    pain: PainScore | Literal["unknown"]
    preferences: Preferences | None = None


class PhotoScreeningRequest(StrictModel):
    """The app's screening photo + questionnaire (ADR 020). The photo is decoded in
    memory for the quality gate and the detector, then dropped: never stored or logged."""

    photo: str = Field(
        min_length=1,
        max_length=MAX_PHOTO_BASE64_CHARS,
        description="Standard base64 (no data: prefix) of the JPEG/PNG photo that passed the "
        "on-device quality gate, <= 512 000 bytes, metadata stripped.",
    )
    symptoms: Symptoms
    age_months: AgeMonths | None
    pain: PainScore | Literal["unknown"]
    preferences: Preferences | None = None


# ------------------------------------------------------------------------- responses
class Reason(ResponseModel):
    id: str
    text: str


class Lesion(ResponseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    detection_index: int
    class_: CariesClass = Field(alias="class")
    dentition: Literal["primary", "permanent"]
    confidence: float
    counted: bool


class TriageOut(ResponseModel):
    level: Level
    reasons: list[Reason]
    lesions: list[Lesion]
    advice_ids: list[str]
    referral_template_id: str


class IdText(ResponseModel):
    id: str
    text: str


class PpmOption(ResponseModel):
    candidate_id: str
    label: str
    min: int
    max: int
    source: str


class Amount(ResponseModel):
    code: str
    text: str


class Supervision(ResponseModel):
    required: bool
    until_age_months: int | None
    text: str | None


class Fluoride(ResponseModel):
    band_id: str
    ppm_options: list[PpmOption]
    decision_pending: bool
    amount: Amount
    supervision: Supervision
    notes: list[IdText]


class ProductOut(ResponseModel):
    id: str
    brand: str
    ppm: int
    bpom_notification_no: str | None
    verified: bool
    verified_at: str | None
    synthetic: bool
    matches_candidate_ids: list[str]


class RecommendationStatus(ResponseModel):
    needs_review: bool
    reasons: list[str]
    generic_message: IdText | None


class RecommendationOut(ResponseModel):
    """Output of ``recommender.recommend`` unchanged (fluoride-only v1). While
    ``status.needs_review`` is true the UI shows ``status.generic_message`` and must
    not present ppm values as clinical advice."""

    fluoride: Fluoride
    disclaimer_ids: list[str]
    disclaimers: list[IdText]
    products: list[ProductOut]
    notes: list[IdText]
    status: RecommendationStatus
    rules_version: str


class RulesVersion(ResponseModel):
    triage: str
    recommender: str


class Referral(ResponseModel):
    required: Literal[True] = True
    cta_id: str = Field(description="Advice id of the referral call-to-action (always present).")
    cta_text: str
    template_id: str = Field(description="Referral letter template for the final level.")


class ScreeningBase(ResponseModel):
    """Fields EVERY successful screening response carries."""

    triage: TriageOut
    recommendation: RecommendationOut | None
    recommendation_status: Literal["ok", "skipped_age_unknown", "skipped_age_out_of_range"]
    rules_version: RulesVersion
    rules_status: RulesStatusField = Field(
        description="draft/needs_review while the triage or recommender release check fails: "
        "the output must NOT be presented as clinically validated."
    )
    clinically_validated: bool
    disclaimer_ids: list[str]
    disclaimers: list[IdText]
    referral: Referral
    request_id: str


class EvaluateResponse(ScreeningBase):
    purpose: Literal["screening_reference"] = "screening_reference"


class StageOut(ResponseModel):
    name: Literal[
        "red_flag", "consent", "photo_fetch", "quality_gate", "detector", "triage", "recommendation"
    ]
    status: Literal["completed", "skipped", "failed", "unavailable"]
    duration_ms: float
    detail: dict[str, str | int | float | bool | list[str] | None] = Field(default_factory=dict)


class QualityOut(ResponseModel):
    passed: bool
    reasons: list[str]
    method: str
    config_status: str


class BoxOut(ResponseModel):
    x1: float
    y1: float
    x2: float
    y2: float


class OverlayDetection(ResponseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    detection_index: int
    class_: CariesClass = Field(alias="class")
    confidence: float
    box: BoxOut


class Overlay(ResponseModel):
    box_use: Literal["ui_overlay_only"] = "ui_overlay_only"
    coordinate_space: Literal["original_image_pixels"] = "original_image_pixels"
    detections: list[OverlayDetection]


class DetectorOut(ResponseModel):
    name: str
    model_version: str


class AnalyzeResponse(ScreeningBase):
    purpose: Literal["qa_second_opinion"] = "qa_second_opinion"
    outcome: Literal["completed", "red_flag_short_circuit", "quality_failed"]
    photo_status: PhotoStatus
    stages: list[StageOut]
    quality: QualityOut | None
    detector: DetectorOut | None
    overlay: Overlay | None = Field(
        description="Boxes for drawing ONLY (ADR 014). Never sent to triage; never use box "
        "size/area for severity."
    )


class PhotoScreeningResponse(ScreeningBase):
    purpose: Literal["screening_photo"] = "screening_photo"
    outcome: Literal["completed", "red_flag_short_circuit", "quality_failed"]
    photo_status: PhotoStatus
    stages: list[StageOut]
    quality: QualityOut | None
    detector: DetectorOut | None
    overlay: Overlay | None = Field(
        description="Boxes in pixels of the photo that was sent, for drawing ONLY (ADR 014). "
        "Never sent to triage; never use box size/area for severity."
    )


class ClassInfo(ResponseModel):
    id: str
    dentition: str
    dataset_label: str
    label: str


class ParamOut(ResponseModel):
    engine: Literal["triage", "recommender", "quality_gate"]
    name: str
    status: str
    placeholder: bool
    reviewed: bool


class GateOut(ResponseModel):
    releasable: bool
    blocker_count: int = Field(description="-1 when the release check could not run.")
    marker_status: str | None = None


class ComponentOut(ResponseModel):
    name: str
    available: bool
    version: str | None = None
    config_status: str | None = None


class RulesResponse(ResponseModel):
    rules_version: RulesVersion
    rules_status: RulesStatusField
    clinically_validated: bool
    notice: str
    release_checks: dict[str, GateOut]
    classes: list[ClassInfo]
    levels: list[Level]
    parameters: list[ParamOut]
    quality_gate: ComponentOut
    detector: ComponentOut
    disclaimer_ids: list[str]
