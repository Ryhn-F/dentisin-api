"""In-memory repository for tests and local development.

It mirrors the database behaviour of 20261009000100_ml_review_queue.sql:
unique image_upload_id (idempotent enqueue), the eligibility guard trigger,
one result per (queue item, grader), review_count maintenance and the
pending -> completed transition.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from . import tables as t
from .base import (
    Cursor,
    DuplicateError,
    ImageUpload,
    ModelRecord,
    NewModel,
    NewReview,
    NotEligibleError,
    QueueClosedError,
    QueueItem,
    ReviewRecord,
)


@dataclass
class AuditEntry:
    actor_id: str | None
    action: str
    target_table: str | None
    target_id: str | None
    details: dict[str, str | int | bool | None]


@dataclass
class InMemoryRepository:
    roles: dict[str, set[str]] = field(default_factory=dict)
    flags: dict[str, bool] = field(default_factory=dict)
    images: dict[str, ImageUpload] = field(default_factory=dict)
    # child_id -> True when an active (not withdrawn) consent (c) exists
    dataset_consent: dict[str, bool] = field(default_factory=dict)
    photos: dict[str, bytes] = field(default_factory=dict)
    queue: dict[str, QueueItem] = field(default_factory=dict)
    reviews: dict[str, tuple[NewReview, ReviewRecord]] = field(default_factory=dict)
    models: dict[str, ModelRecord] = field(default_factory=dict)
    audit_log: list[AuditEntry] = field(default_factory=list)
    healthy: bool = True

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _eligible(self, image: ImageUpload) -> bool:
        return (
            self.flags.get(t.FLAG_DATASET_CONTRIBUTION, False)
            and image.status == t.IMAGE_STATUS_UPLOADED
            and image.expires_at > self._now()
            and self.dataset_consent.get(image.child_id, False)
        )

    async def ping(self) -> bool:
        return self.healthy

    async def get_roles(self, user_id: str) -> frozenset[str]:
        return frozenset(self.roles.get(user_id, set()))

    async def flag_enabled(self, key: str) -> bool:
        return self.flags.get(key, False)

    async def get_image_upload(self, image_upload_id: str) -> ImageUpload | None:
        return self.images.get(image_upload_id)

    async def children_with_active_dataset_consent(self, child_ids: set[str]) -> set[str]:
        return {c for c in child_ids if self.dataset_consent.get(c, False)}

    async def enqueue_review(
        self, image_upload_id: str, enqueued_by: str, required_reviews: int
    ) -> tuple[QueueItem, bool]:
        for item in self.queue.values():
            if item.image_upload_id == image_upload_id:
                return item, False
        image = self.images.get(image_upload_id)
        if image is None or not self._eligible(image):
            raise NotEligibleError
        item = QueueItem(
            id=str(uuid.uuid4()),
            image_upload_id=image_upload_id,
            status=t.RQ_STATUS_PENDING,
            required_reviews=required_reviews,
            review_count=0,
            enqueued_at=self._now(),
        )
        self.queue[item.id] = item
        return item, True

    async def list_pending(
        self, limit: int, after: Cursor | None, now: datetime
    ) -> list[tuple[QueueItem, ImageUpload]]:
        rows: list[tuple[QueueItem, ImageUpload]] = []
        for item in sorted(self.queue.values(), key=lambda q: (q.enqueued_at, q.id)):
            if item.status != t.RQ_STATUS_PENDING:
                continue
            if after is not None and (item.enqueued_at, item.id) <= after:
                continue
            image = self.images.get(item.image_upload_id)
            if image is None or image.status != t.IMAGE_STATUS_UPLOADED or image.expires_at <= now:
                continue
            rows.append((item, image))
            if len(rows) >= limit:
                break
        return rows

    async def get_queue_item(self, queue_id: str) -> QueueItem | None:
        return self.queue.get(queue_id)

    async def add_review(self, review: NewReview) -> tuple[ReviewRecord, QueueItem]:
        item = self.queue.get(review.queue_id)
        if item is None or item.status != t.RQ_STATUS_PENDING:
            raise QueueClosedError
        image = self.images.get(item.image_upload_id)
        if image is None or not self._eligible(image):
            raise NotEligibleError
        for existing, _ in self.reviews.values():
            if (
                existing.queue_id == review.queue_id
                and existing.grader_pseudonym == review.grader_pseudonym
            ):
                raise DuplicateError
        record = ReviewRecord(
            id=str(uuid.uuid4()),
            queue_id=review.queue_id,
            grader_pseudonym=review.grader_pseudonym,
            decision=review.decision,
            created_at=self._now(),
        )
        self.reviews[record.id] = (review, record)
        count = item.review_count + 1
        done = count >= item.required_reviews
        item = replace(
            item,
            review_count=count,
            status=t.RQ_STATUS_COMPLETED if done else t.RQ_STATUS_PENDING,
            completed_at=self._now() if done else None,
        )
        self.queue[item.id] = item
        return record, item

    async def latest_model(self) -> ModelRecord | None:
        if not self.models:
            return None
        return max(self.models.values(), key=lambda m: m.sequence)

    async def get_model_by_sha(self, manifest_sha256: str) -> ModelRecord | None:
        return self.models.get(manifest_sha256)

    async def insert_model(self, model: NewModel) -> ModelRecord:
        if model.manifest_sha256 in self.models or any(
            m.sequence == model.sequence for m in self.models.values()
        ):
            raise DuplicateError
        record = ModelRecord(
            id=str(uuid.uuid4()),
            manifest_sha256=model.manifest_sha256,
            sequence=model.sequence,
            schema_version=model.schema_version,
            channel=model.channel,
            signer_key_id=model.signer_key_id,
            min_app_version=model.min_app_version,
            model_version=model.model_version,
            model_sha256=model.model_sha256,
            model_license=model.model_license,
            rules_version=model.rules_version,
            recommender_version=model.recommender_version,
            manifest_created_at=model.manifest_created_at,
            expires_at=model.expires_at,
            registered_at=self._now(),
        )
        self.models[record.manifest_sha256] = record
        return record

    async def audit(
        self,
        actor_id: str | None,
        action: str,
        target_table: str | None,
        target_id: str | None,
        details: dict[str, str | int | bool | None],
    ) -> None:
        self.audit_log.append(AuditEntry(actor_id, action, target_table, target_id, details))

    async def fetch_photo(self, object_path: str, max_bytes: int) -> bytes:
        data = self.photos.get(object_path, b"")
        if len(data) > max_bytes:
            raise ValueError("photo too large")
        return data
