"""Review-queue business rules (consent gating, idempotency, audit).

Gating order (first failure wins, so a disabled feature reveals nothing else):
1. feature flag ``dataset_contribution`` ON          else 409 dataset_contribution_disabled
2. image_upload exists                               else 404 image_upload_not_found
3. image status 'uploaded' (not withdrawn/deleted) and not past expires_at
                                                     else 409 image_not_eligible
4. ACTIVE consent (c) for the child (not withdrawn)  else 409 consent_inactive
The DB guard trigger re-checks 1, 3 and 4 inside the INSERT (race-safe).
"""

from __future__ import annotations

import base64
import binascii
import uuid
from datetime import UTC, datetime

from ..auth import Principal
from ..errors import ApiError, conflict, not_found
from ..repositories import tables as t
from ..repositories.base import (
    Cursor,
    DuplicateError,
    ImageUpload,
    MlRepository,
    NewReview,
    NotEligibleError,
    QueueClosedError,
    QueueItem,
    ReviewRecord,
)

ERR_FLAG_OFF = ("dataset_contribution_disabled", "Dataset contribution is disabled.")
ERR_IMAGE_NOT_FOUND = ("image_upload_not_found", "Image upload not found.")
ERR_IMAGE_NOT_ELIGIBLE = (
    "image_not_eligible",
    "Photo is withdrawn, deleted or expired and cannot be reviewed.",
)
ERR_CONSENT = ("consent_inactive", "No active dataset consent (c) for this photo.")


async def ensure_flag(repo: MlRepository) -> None:
    if not await repo.flag_enabled(t.FLAG_DATASET_CONTRIBUTION):
        raise conflict(*ERR_FLAG_OFF)


async def eligible_image(repo: MlRepository, image_upload_id: str) -> ImageUpload:
    """Run gates 1-4 for one photo; returns the (internal) image record."""
    await ensure_flag(repo)
    image = await repo.get_image_upload(image_upload_id)
    if image is None:
        raise not_found(*ERR_IMAGE_NOT_FOUND)
    if image.status != t.IMAGE_STATUS_UPLOADED or image.expires_at <= datetime.now(UTC):
        raise conflict(*ERR_IMAGE_NOT_ELIGIBLE)
    if image.child_id not in await repo.children_with_active_dataset_consent({image.child_id}):
        raise conflict(*ERR_CONSENT)
    return image


async def enqueue(
    repo: MlRepository, principal: Principal, image_upload_id: str, required_reviews: int
) -> tuple[QueueItem, bool]:
    await eligible_image(repo, image_upload_id)
    try:
        item, created = await repo.enqueue_review(
            image_upload_id, principal.user_id, required_reviews
        )
    except NotEligibleError:
        raise conflict(*ERR_IMAGE_NOT_ELIGIBLE) from None
    if created:
        await repo.audit(
            principal.user_id,
            "ml_review_enqueued",
            t.REVIEW_QUEUE,
            item.id,
            {"required_reviews": required_reviews},
        )
    return item, created


def encode_cursor(item: QueueItem) -> str:
    raw = f"{item.enqueued_at.isoformat()}|{item.id}".encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> Cursor:
    invalid = ApiError(422, "invalid_cursor", "Invalid pagination cursor.")
    if len(cursor) > 200:
        raise invalid
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("ascii")
        ts, _, item_id = raw.partition("|")
        when = datetime.fromisoformat(ts)
        item_id = str(uuid.UUID(item_id))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise invalid from None
    if when.tzinfo is None:
        raise invalid
    return when, item_id


async def list_pending(
    repo: MlRepository, principal: Principal, limit: int, cursor: str | None
) -> tuple[list[QueueItem], str | None]:
    await ensure_flag(repo)
    after = decode_cursor(cursor) if cursor else None
    rows = await repo.list_pending(limit, after, datetime.now(UTC))
    consented = await repo.children_with_active_dataset_consent({img.child_id for _, img in rows})
    items = [item for item, img in rows if img.child_id in consented]
    next_cursor = encode_cursor(rows[-1][0]) if len(rows) == limit else None
    await repo.audit(
        principal.user_id, "ml_review_queue_listed", t.REVIEW_QUEUE, None, {"count": len(items)}
    )
    return items, next_cursor


async def submit_review(
    repo: MlRepository,
    principal: Principal,
    queue_id: str,
    review: NewReview,
) -> tuple[ReviewRecord, QueueItem]:
    await ensure_flag(repo)
    item = await repo.get_queue_item(queue_id)
    if item is None:
        raise not_found("queue_item_not_found", "Review queue item not found.")
    if item.status != t.RQ_STATUS_PENDING:
        raise conflict("queue_item_closed", "This review queue item is no longer open.")
    await eligible_image(repo, item.image_upload_id)
    try:
        record, updated = await repo.add_review(review)
    except DuplicateError:
        raise conflict(
            "already_reviewed", "This grader already reviewed this queue item."
        ) from None
    except QueueClosedError:
        raise conflict("queue_item_closed", "This review queue item is no longer open.") from None
    except NotEligibleError:
        raise conflict(*ERR_IMAGE_NOT_ELIGIBLE) from None
    await repo.audit(
        principal.user_id,
        "ml_review_submitted",
        t.REVIEW_RESULT,
        record.id,
        {"decision": review.decision, "boxes": len(review.boxes), "status": updated.status},
    )
    return record, updated
