"""Review-queue request/response models.

Boxes follow the canonical annotation convention of ml/detection/canonical.py
(``dentis-canonical-v1``): class NAME + absolute pixel corners in the ORIGINAL
image, top-left origin, ``0 <= x1 < x2 <= width``, ``0 <= y1 < y2 <= height``,
minimum side 1 px. v1 classes (ADR 001/003): caries_cavitated,
caries_noncavitated.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Final, Literal, Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .common import ResponseModel, StrictModel

CLASS_NAMES: Final = ("caries_cavitated", "caries_noncavitated")
MIN_BOX_SIDE_PX: Final = 1.0
MAX_IMAGE_SIDE_PX: Final = 20_000
MAX_BOXES: Final = 100
MAX_NOTES_CHARS: Final = 500
_CONTROL_CHARS: Final = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

Coord = Annotated[float, Field(ge=0, le=MAX_IMAGE_SIDE_PX, allow_inf_nan=False, strict=True)]
CariesClass = Literal["caries_cavitated", "caries_noncavitated"]
Decision = Literal["annotated", "no_finding", "ungradable"]


class EnqueueRequest(StrictModel):
    image_upload_id: UUID = Field(
        description="public.image_upload.id of a consented dataset photo. Never a photo or path."
    )
    required_reviews: int = Field(
        default=2,
        strict=True,
        ge=1,
        le=5,
        description="Independent dentist reviews needed before the item is complete "
        "(>= 2 recommended for inter-rater agreement).",
    )


class QueueItemOut(ResponseModel):
    id: UUID
    image_upload_id: UUID
    status: Literal["pending", "completed", "cancelled"]
    required_reviews: int
    review_count: int
    enqueued_at: datetime
    completed_at: datetime | None = None


class EnqueueResponse(ResponseModel):
    item: QueueItemOut
    created: bool = Field(description="false when the photo was already queued (idempotent).")


class QueueListResponse(ResponseModel):
    items: list[QueueItemOut]
    next_cursor: str | None = Field(
        default=None, description="Opaque cursor for the next page; null on the last page."
    )


class Box(StrictModel):
    cls: CariesClass
    x1: Coord
    y1: Coord
    x2: Coord
    y2: Coord

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.x2 - self.x1 < MIN_BOX_SIDE_PX or self.y2 - self.y1 < MIN_BOX_SIDE_PX:
            raise ValueError("box must satisfy x1 < x2, y1 < y2 with sides >= 1 px")
        return self


class ReviewRequest(StrictModel):
    grader_pseudonym: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{1,31}$",
        description="Pseudonymous grader id (e.g. drg-a1). Never a real name.",
    )
    decision: Decision
    image_width: int = Field(ge=1, le=MAX_IMAGE_SIDE_PX, strict=True)
    image_height: int = Field(ge=1, le=MAX_IMAGE_SIDE_PX, strict=True)
    boxes: list[Box] = Field(default_factory=list, max_length=MAX_BOXES)
    notes: str | None = Field(default=None, max_length=MAX_NOTES_CHARS)

    @field_validator("notes")
    @classmethod
    def _no_control_chars(cls, value: str | None) -> str | None:
        if value is not None and _CONTROL_CHARS.search(value):
            raise ValueError("notes must not contain control characters")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.decision == "annotated" and not self.boxes:
            raise ValueError("decision 'annotated' requires at least one box")
        if self.decision != "annotated" and self.boxes:
            raise ValueError("boxes are only allowed with decision 'annotated'")
        for box in self.boxes:
            if box.x2 > self.image_width or box.y2 > self.image_height:
                raise ValueError("box lies outside the image")
        return self


class ReviewResponse(ResponseModel):
    review_id: UUID
    queue_id: UUID
    queue_status: Literal["pending", "completed", "cancelled"]
    review_count: int
    required_reviews: int
