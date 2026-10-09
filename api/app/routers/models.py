from __future__ import annotations

from fastapi import APIRouter, Response

from ..deps import AdminDep, StateDep
from ..schemas.common import error_responses
from ..schemas.models import ModelOut, ModelRegisterRequest, ModelRegisterResponse
from ..services import model_registry as svc

router = APIRouter(prefix="/v1/ml/models", tags=["model-registry"])


@router.post(
    "",
    response_model=ModelRegisterResponse,
    status_code=201,
    responses={
        200: {"model": ModelRegisterResponse, "description": "Already registered (idempotent)."},
        **error_responses(401, 403, 409, 413, 422, 429, 503),
    },
    summary="Register a model/rules version from a signed manifest (admin; verify-only)",
)
async def register_model(
    body: ModelRegisterRequest, principal: AdminDep, state: StateDep, response: Response
) -> ModelRegisterResponse:
    record, created = await svc.register(
        state.repo,
        state.trust_store,
        state.settings.production,
        principal,
        body.manifest_json,
        body.manifest_sig,
    )
    if not created:
        response.status_code = 200
    return ModelRegisterResponse(
        model=ModelOut.model_validate(record, from_attributes=True), created=created
    )
