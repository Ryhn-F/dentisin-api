from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from .common import ResponseModel, StrictModel
from .review import CariesClass

# Principle 1 (CLAUDE.md): screening, not diagnosis. Shown with every result.
DISCLAIMER_ID = (
    "Hasil ini hanya indikasi skrining, bukan diagnosis. "
    "Perlu diperiksa oleh dokter gigi atau fasilitas kesehatan."
)


class InferRequest(StrictModel):
    image_upload_id: UUID = Field(
        description="public.image_upload.id of a consented dataset photo (storage reference). "
        "Raw photos are never accepted in the body."
    )


class DetectionOut(ResponseModel):
    cls: CariesClass
    x1: float
    y1: float
    x2: float
    y2: float
    score: float = Field(ge=0, le=1)


class InferResponse(ResponseModel):
    detector: str
    model_version: str
    detections: list[DetectionOut]
    purpose: Literal["qa_second_opinion"] = "qa_second_opinion"
    disclaimer: str = DISCLAIMER_ID
