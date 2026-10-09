from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from ..deps import AdminDep, ReviewerDep, StateDep
from ..repositories.base import NewReview, QueueItem
from ..schemas.common import error_responses
from ..schemas.review import (
    EnqueueRequest,
    EnqueueResponse,
    QueueItemOut,
    QueueListResponse,
    ReviewRequest,
    ReviewResponse,
)
from ..services import review_queue as svc

router = APIRouter(prefix="/v1/ml/review-queue", tags=["review-queue"])


def _out(item: QueueItem) -> QueueItemOut:
    return QueueItemOut.model_validate(item, from_attributes=True)


@router.post(
    "",
    response_model=EnqueueResponse,
    status_code=201,
    responses={
        200: {"model": EnqueueResponse, "description": "Already queued (idempotent)."},
        **error_responses(401, 403, 404, 409, 413, 422, 429, 503),
    },
    summary="Enqueue a consented dataset photo for dentist review (admin)",
)
async def enqueue(
    body: EnqueueRequest, principal: AdminDep, state: StateDep, response: Response
) -> EnqueueResponse:
    item, created = await svc.enqueue(
        state.repo, principal, str(body.image_upload_id), body.required_reviews
    )
    if not created:
        response.status_code = 200
    return EnqueueResponse(item=_out(item), created=created)


@router.get(
    "",
    response_model=QueueListResponse,
    responses=error_responses(401, 403, 409, 422, 429, 503),
    summary="List pending review items (reviewer/admin), keyset-paginated",
)
async def list_pending(
    principal: ReviewerDep,
    state: StateDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> QueueListResponse:
    items, next_cursor = await svc.list_pending(state.repo, principal, limit, cursor)
    return QueueListResponse(items=[_out(i) for i in items], next_cursor=next_cursor)


@router.post(
    "/{queue_id}/review",
    response_model=ReviewResponse,
    status_code=201,
    responses=error_responses(401, 403, 404, 409, 413, 422, 429, 503),
    summary="Submit one dentist review (boxes in canonical format) for a queue item",
)
async def submit_review(
    queue_id: UUID, body: ReviewRequest, principal: ReviewerDep, state: StateDep
) -> ReviewResponse:
    review = NewReview(
        queue_id=str(queue_id),
        reviewer_id=principal.user_id,
        grader_pseudonym=body.grader_pseudonym,
        decision=body.decision,
        image_width=body.image_width,
        image_height=body.image_height,
        boxes=[b.model_dump() for b in body.boxes],
        notes=body.notes,
    )
    record, item = await svc.submit_review(state.repo, principal, str(queue_id), review)
    return ReviewResponse(
        review_id=UUID(record.id),
        queue_id=UUID(item.id),
        queue_status=item.status,  # type: ignore[arg-type]
        review_count=item.review_count,
        required_reviews=item.required_reviews,
    )
