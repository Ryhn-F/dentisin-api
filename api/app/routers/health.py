from __future__ import annotations

from fastapi import APIRouter

from ..deps import StateDep
from ..errors import unavailable
from ..schemas.common import StatusResponse, error_responses

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=StatusResponse, summary="Liveness (no data, no I/O)")
async def healthz() -> StatusResponse:
    return StatusResponse(status="ok")


@router.get(
    "/readyz",
    response_model=StatusResponse,
    responses=error_responses(503),
    summary="Readiness: configuration complete and Supabase reachable",
)
async def readyz(state: StateDep) -> StatusResponse:
    # Never reveal WHICH setting is missing or why upstream failed.
    if state.settings.missing_for_readiness() or not await state.repo.ping():
        raise unavailable("not_ready", "Service is not ready.")
    return StatusResponse(status="ready")
