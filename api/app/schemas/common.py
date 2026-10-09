from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Request models: unknown fields rejected. Numeric fields are declared
    ``strict=True`` individually (no "2" -> 2 coercion); UUIDs accept their
    JSON string form."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ErrorDetail(ResponseModel):
    code: str
    message: str
    request_id: str
    details: list[dict[str, Any]] | None = None


class ErrorResponse(ResponseModel):
    error: ErrorDetail


class StatusResponse(ResponseModel):
    status: str


def error_responses(*codes: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI `responses=` entries that all use the shared error model."""
    return {code: {"model": ErrorResponse} for code in codes}
