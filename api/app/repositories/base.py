"""Data-access interface. Two implementations:
- ``SupabaseRepository`` (PostgREST + Storage over httpx with the service_role
  key; server-side only), and
- ``InMemoryRepository`` (tests / local dev without Supabase).

Records never carry more than the API needs. ``ImageUpload.object_path`` is
kept internal (only the optional inference hook reads it) and is never
returned to clients or logged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


class RepositoryError(Exception):
    """Upstream (Supabase) failure. Carries only the HTTP status, never bodies."""

    def __init__(self, upstream_status: int | None = None) -> None:
        super().__init__("repository_error")
        self.upstream_status = upstream_status


class DuplicateError(Exception):
    pass


class NotEligibleError(Exception):
    """DB guard trigger refused: photo lacks active consent (c), is withdrawn/
    expired, or dataset_contribution is off."""


class QueueClosedError(Exception):
    pass


@dataclass(frozen=True)
class ImageUpload:
    id: str
    child_id: str
    status: str
    expires_at: datetime
    object_path: str = field(repr=False)


@dataclass(frozen=True)
class QueueItem:
    id: str
    image_upload_id: str
    status: str
    required_reviews: int
    review_count: int
    enqueued_at: datetime
    completed_at: datetime | None = None


@dataclass(frozen=True)
class NewReview:
    queue_id: str
    reviewer_id: str
    grader_pseudonym: str
    decision: str
    image_width: int
    image_height: int
    boxes: list[dict[str, Any]]
    notes: str | None


@dataclass(frozen=True)
class ReviewRecord:
    id: str
    queue_id: str
    grader_pseudonym: str
    decision: str
    created_at: datetime


@dataclass(frozen=True)
class NewModel:
    manifest_sha256: str
    sequence: int
    schema_version: int
    channel: str
    signer_key_id: str
    min_app_version: str
    model_version: str
    model_sha256: str
    model_license: str
    rules_version: str
    recommender_version: str
    manifest_created_at: datetime
    expires_at: datetime | None
    manifest_canonical: str
    manifest_sig: str
    registered_by: str


@dataclass(frozen=True)
class ModelRecord:
    id: str
    manifest_sha256: str
    sequence: int
    schema_version: int
    channel: str
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


Cursor = tuple[datetime, str]


class MlRepository(Protocol):
    async def ping(self) -> bool: ...

    async def get_roles(self, user_id: str) -> frozenset[str]: ...

    async def flag_enabled(self, key: str) -> bool: ...

    async def get_image_upload(self, image_upload_id: str) -> ImageUpload | None: ...

    async def children_with_active_dataset_consent(self, child_ids: set[str]) -> set[str]: ...

    async def enqueue_review(
        self, image_upload_id: str, enqueued_by: str, required_reviews: int
    ) -> tuple[QueueItem, bool]: ...

    async def list_pending(
        self, limit: int, after: Cursor | None, now: datetime
    ) -> list[tuple[QueueItem, ImageUpload]]: ...

    async def get_queue_item(self, queue_id: str) -> QueueItem | None: ...

    async def add_review(self, review: NewReview) -> tuple[ReviewRecord, QueueItem]: ...

    async def latest_model(self) -> ModelRecord | None: ...

    async def get_model_by_sha(self, manifest_sha256: str) -> ModelRecord | None: ...

    async def insert_model(self, model: NewModel) -> ModelRecord: ...

    async def audit(
        self,
        actor_id: str | None,
        action: str,
        target_table: str | None,
        target_id: str | None,
        details: dict[str, str | int | bool | None],
    ) -> None: ...

    async def fetch_photo(self, object_path: str, max_bytes: int) -> bytes: ...
