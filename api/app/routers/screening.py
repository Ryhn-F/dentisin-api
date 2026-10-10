"""``/v1/screening`` — stateless screening reference pipeline (ADR 002 option 3:
medical module behind ``ENABLE_SCREENING_API``, default off -> 404).

SCREENING, NOT DIAGNOSIS: every 200 response carries ``rules_status``,
``clinically_validated``, ``disclaimer_ids`` and the referral CTA. Nothing is
persisted; devices sync their own results through the Supabase ``sync_events`` RPC.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError

from ..deps import AdminDep, AnyRoleDep, AppState, ScreenerDep, StateDep
from ..errors import ApiError, not_found, request_id_of, validation_mappers
from ..schemas.common import error_responses
from ..schemas.screening import (
    AnalyzeRequest,
    AnalyzeResponse,
    EvaluateRequest,
    EvaluateResponse,
    RulesResponse,
)
from ..services import screening as svc
from ..services.detector import NullDetector
from ..services.screening_rules import DISCLAIMER_ID, RULES_NOT_VALIDATED_ID, ScreeningRules

PREFIX = "/v1/screening"


async def require_screening_enabled(state: StateDep) -> None:
    """Runs BEFORE authentication: a disabled module looks like a missing route."""
    if not state.settings.ENABLE_SCREENING_API:
        raise not_found()


async def require_cloud_infer_enabled(state: StateDep) -> None:
    if not state.settings.ENABLE_CLOUD_INFER:
        raise not_found()


router = APIRouter(
    prefix=PREFIX, tags=["screening"], dependencies=[Depends(require_screening_enabled)]
)


def _rules(state_rules: ScreeningRules | None) -> ScreeningRules:
    if state_rules is None:
        raise svc.screening_error(
            None, 503, "rules_unavailable", "Screening rules are unavailable."
        )
    return state_rules


def _payload(envelope: svc.Envelope, request: Request) -> dict[str, Any]:
    return {
        "triage": envelope.triage,
        "recommendation": envelope.recommendation,
        "recommendation_status": envelope.recommendation_status,
        "rules_version": envelope.rules_version,
        "rules_status": envelope.rules_status,
        "clinically_validated": envelope.clinically_validated,
        "disclaimer_ids": envelope.disclaimer_ids,
        "disclaimers": envelope.disclaimers,
        "referral": envelope.referral,
        "request_id": request_id_of(request),
    }


@router.post(
    "/evaluate",
    response_model=EvaluateResponse,
    response_model_by_alias=True,
    responses=error_responses(401, 403, 404, 413, 422, 429, 503),
    summary="Stateless triage + toothpaste recommendation from device-provided inputs",
)
async def evaluate(
    body: EvaluateRequest, principal: ScreenerDep, state: StateDep, request: Request
) -> EvaluateResponse:
    del principal  # authorisation only; nothing about the user is used or stored
    rules = _rules(state.screening)
    envelope = svc.evaluate(rules, body)
    return EvaluateResponse.model_validate(_payload(envelope, request))


@router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    response_model_by_alias=True,
    dependencies=[Depends(require_cloud_infer_enabled)],
    responses=error_responses(401, 403, 404, 409, 413, 422, 429, 503),
    summary="QA/second-opinion pipeline on a consented dataset photo reference "
    "(404 unless ENABLE_SCREENING_API and ENABLE_CLOUD_INFER)",
)
async def analyze(
    body: AnalyzeRequest, principal: AdminDep, state: StateDep, request: Request
) -> AnalyzeResponse:
    rules = _rules(state.screening)
    result = await svc.analyze(
        rules, body, state.repo, state.detector, state.quality_gate, principal
    )
    payload = _payload(result.envelope, request)
    payload.update(
        {
            "outcome": result.outcome,
            "photo_status": result.photo_status,
            "stages": result.stages,
            "quality": None
            if result.quality is None
            else {
                "passed": result.quality.passed,
                "reasons": list(result.quality.reasons),
                "method": result.quality.method,
                "config_status": state.quality_gate.config_status,
            },
            "detector": None
            if result.detector is None
            else {"name": result.detector.name, "model_version": result.detector.model_version},
            "overlay": None
            if result.overlay is None
            else {
                "detections": [
                    {
                        "detection_index": index,
                        "class": det.cls,
                        "confidence": det.score,
                        "box": {"x1": det.x1, "y1": det.y1, "x2": det.x2, "y2": det.y2},
                    }
                    for index, det in enumerate(result.overlay)
                ]
            },
        }
    )
    return AnalyzeResponse.model_validate(payload)


@router.get(
    "/rules",
    response_model=RulesResponse,
    responses=error_responses(401, 403, 404, 429, 503),
    summary="Rule metadata: versions, status, classes and which parameters are placeholders",
)
async def rules_metadata(principal: AnyRoleDep, state: StateDep) -> RulesResponse:
    del principal
    rules = _rules(state.screening)
    gate = state.quality_gate
    params: list[dict[str, Any]] = [
        {
            "engine": p.engine,
            "name": p.name,
            "status": p.status,
            "placeholder": p.placeholder,
            "reviewed": p.reviewed,
        }
        for p in rules.params
    ]
    params.append(
        {
            "engine": "quality_gate",
            "name": "quality_gate.yaml",
            "status": gate.config_status,
            "placeholder": gate.config_status != "SET",
            "reviewed": gate.config_status == "SET",
        }
    )
    approved = rules.rules_status == "approved"
    return RulesResponse.model_validate(
        {
            "rules_version": {
                "triage": rules.triage_version,
                "recommender": rules.recommender_version,
            },
            "rules_status": rules.rules_status,
            "clinically_validated": approved,
            "notice": rules.notice,
            "release_checks": {
                "triage": vars(rules.triage_gate),
                "recommender": vars(rules.recommender_gate),
            },
            "classes": [
                {
                    "id": cls["id"],
                    "dentition": cls["dentition"],
                    "dataset_label": cls["dataset_label"],
                    "label": cls["label"],
                }
                for cls in rules.triage_rules["detection_classes"]
            ],
            "levels": [
                lv["id"] for lv in sorted(rules.triage_rules["levels"], key=lambda lv: -lv["rank"])
            ],
            "parameters": params,
            "quality_gate": {
                "name": gate.name,
                "available": gate.available,
                "config_status": gate.config_status,
            },
            "detector": {
                "name": state.detector.name,
                "available": not isinstance(state.detector, NullDetector),
                "version": state.detector.model_version,
            },
            "disclaimer_ids": [DISCLAIMER_ID] + ([] if approved else [RULES_NOT_VALIDATED_ID]),
        }
    )


@router.get(
    "/schema/input",
    responses=error_responses(401, 403, 404, 429, 503),
    summary="Triage engine input JSON Schema (triage/dist/input.schema.json, verbatim)",
)
async def input_schema(principal: AnyRoleDep, state: StateDep) -> dict[str, Any]:
    del principal
    return dict(_rules(state.screening).input_schema)


@router.get(
    "/schema/output",
    responses=error_responses(401, 403, 404, 429, 503),
    summary="Triage engine output JSON Schema (triage/dist/output.schema.json, verbatim)",
)
async def output_schema(principal: AnyRoleDep, state: StateDep) -> dict[str, Any]:
    del principal
    return dict(_rules(state.screening).output_schema)


def install(app: FastAPI) -> None:
    """Mount the router and register the engine-code validation mappers."""
    app.include_router(router)

    def _rules_of_app() -> ScreeningRules | None:
        state: AppState = app.state.dentisai
        return state.screening

    def _evaluate_mapper(body: Any, exc: RequestValidationError) -> ApiError | None:
        return svc.map_evaluate_validation(_rules_of_app(), body, exc)

    def _analyze_mapper(body: Any, exc: RequestValidationError) -> ApiError | None:
        return svc.map_analyze_validation(_rules_of_app(), body, exc)

    mappers = validation_mappers(app)
    mappers[f"{PREFIX}/evaluate"] = _evaluate_mapper
    mappers[f"{PREFIX}/analyze"] = _analyze_mapper
