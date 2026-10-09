from __future__ import annotations

from fastapi import APIRouter, Depends

from ..deps import AdminDep, StateDep
from ..errors import ApiError, not_found
from ..schemas.common import error_responses
from ..schemas.infer import DetectionOut, InferRequest, InferResponse
from ..services import inference as svc

router = APIRouter(prefix="/v1/ml", tags=["inference"])


async def require_infer_enabled(state: StateDep) -> None:
    """Runs BEFORE authentication so a disabled hook is indistinguishable from
    a route that does not exist."""
    if not state.settings.ENABLE_CLOUD_INFER:
        raise not_found()


@router.post(
    "/infer",
    response_model=InferResponse,
    dependencies=[Depends(require_infer_enabled)],
    responses=error_responses(401, 403, 404, 409, 413, 422, 429, 503),
    summary="OPTIONAL QA/second-opinion inference (404 unless ENABLE_CLOUD_INFER=true)",
)
async def infer(body: InferRequest, principal: AdminDep, state: StateDep) -> InferResponse:
    try:
        detections = await svc.infer(
            state.repo, state.detector, principal, str(body.image_upload_id)
        )
    except ValueError:
        raise ApiError(409, "photo_not_processable", "The photo could not be processed.") from None
    return InferResponse(
        detector=state.detector.name,
        model_version=state.detector.model_version,
        detections=[DetectionOut.model_validate(d, from_attributes=True) for d in detections],
    )
