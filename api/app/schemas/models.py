from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from .common import ResponseModel, StrictModel


class ModelRegisterRequest(StrictModel):
    manifest_json: str = Field(
        min_length=2,
        max_length=16 * 1024,
        description="The EXACT bytes of manifest.json (DCJ-1 canonical, printable ASCII). "
        "All registry metadata is derived from it after verification; nothing else is trusted.",
    )
    manifest_sig: str = Field(
        min_length=2,
        max_length=512,
        description='The exact manifest.sig: {"alg":"Ed25519","key_id":...,"sig":...}. '
        "The server only verifies; it never signs.",
    )


class ModelOut(ResponseModel):
    id: UUID
    manifest_sha256: str
    sequence: int
    schema_version: int
    channel: Literal["production", "internal-test"]
    signer_key_id: str
    min_app_version: str
    model_version: str
    model_sha256: str
    model_license: str
    rules_version: str
    recommender_version: str
    manifest_created_at: datetime
    expires_at: datetime | None
    registered_at: datetime


class ModelRegisterResponse(ResponseModel):
    model: ModelOut
    created: bool = Field(description="false when this exact manifest was already registered.")
