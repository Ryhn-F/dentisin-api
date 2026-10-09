"""Supabase implementation over PostgREST (+ Storage) using the service_role key.

- The key is only ever placed in request headers; it is never logged.
- Upstream failures become ``RepositoryError(upstream_status)``; response
  bodies are never propagated (they may echo data).
- The DB guard triggers in 20261009000100_ml_review_queue.sql re-check
  eligibility, so a race between our checks and a consent withdrawal still
  cannot enqueue/review an ineligible photo.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx

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
    RepositoryError,
    ReviewRecord,
)

_QUEUE_COLS = ",".join(
    (
        t.RQ_ID,
        t.RQ_IMAGE_UPLOAD_ID,
        t.RQ_STATUS,
        t.RQ_REQUIRED_REVIEWS,
        t.RQ_REVIEW_COUNT,
        t.RQ_ENQUEUED_AT,
        t.RQ_COMPLETED_AT,
    )
)
_IMAGE_COLS = ",".join(
    (
        t.IMAGE_UPLOAD_ID,
        t.IMAGE_UPLOAD_CHILD_ID,
        t.IMAGE_UPLOAD_STATUS,
        t.IMAGE_UPLOAD_EXPIRES_AT,
        t.IMAGE_UPLOAD_OBJECT_PATH,
    )
)
_MODEL_COLS = ",".join(
    (
        t.MR_ID,
        t.MR_MANIFEST_SHA256,
        t.MR_SEQUENCE,
        t.MR_SCHEMA_VERSION,
        t.MR_CHANNEL,
        t.MR_SIGNER_KEY_ID,
        t.MR_MIN_APP_VERSION,
        t.MR_MODEL_VERSION,
        t.MR_MODEL_SHA256,
        t.MR_MODEL_LICENSE,
        t.MR_RULES_VERSION,
        t.MR_RECOMMENDER_VERSION,
        t.MR_MANIFEST_CREATED_AT,
        t.MR_EXPIRES_AT,
        t.MR_REGISTERED_AT,
    )
)


def _ts(value: Any) -> datetime:
    if not isinstance(value, str):
        raise RepositoryError()
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _opt_ts(value: Any) -> datetime | None:
    return None if value is None else _ts(value)


def _uuid(value: str) -> str:
    """Normalise and validate a UUID before it is put into a PostgREST filter."""
    return str(uuid.UUID(value))


def _queue_item(row: dict[str, Any]) -> QueueItem:
    return QueueItem(
        id=str(row[t.RQ_ID]),
        image_upload_id=str(row[t.RQ_IMAGE_UPLOAD_ID]),
        status=str(row[t.RQ_STATUS]),
        required_reviews=int(row[t.RQ_REQUIRED_REVIEWS]),
        review_count=int(row[t.RQ_REVIEW_COUNT]),
        enqueued_at=_ts(row[t.RQ_ENQUEUED_AT]),
        completed_at=_opt_ts(row.get(t.RQ_COMPLETED_AT)),
    )


def _image(row: dict[str, Any]) -> ImageUpload:
    return ImageUpload(
        id=str(row[t.IMAGE_UPLOAD_ID]),
        child_id=str(row[t.IMAGE_UPLOAD_CHILD_ID]),
        status=str(row[t.IMAGE_UPLOAD_STATUS]),
        expires_at=_ts(row[t.IMAGE_UPLOAD_EXPIRES_AT]),
        object_path=str(row.get(t.IMAGE_UPLOAD_OBJECT_PATH, "")),
    )


def _model(row: dict[str, Any]) -> ModelRecord:
    return ModelRecord(
        id=str(row[t.MR_ID]),
        manifest_sha256=str(row[t.MR_MANIFEST_SHA256]),
        sequence=int(row[t.MR_SEQUENCE]),
        schema_version=int(row[t.MR_SCHEMA_VERSION]),
        channel=str(row[t.MR_CHANNEL]),
        signer_key_id=str(row[t.MR_SIGNER_KEY_ID]),
        min_app_version=str(row[t.MR_MIN_APP_VERSION]),
        model_version=str(row[t.MR_MODEL_VERSION]),
        model_sha256=str(row[t.MR_MODEL_SHA256]),
        model_license=str(row[t.MR_MODEL_LICENSE]),
        rules_version=str(row[t.MR_RULES_VERSION]),
        recommender_version=str(row[t.MR_RECOMMENDER_VERSION]),
        manifest_created_at=_ts(row[t.MR_MANIFEST_CREATED_AT]),
        expires_at=_opt_ts(row.get(t.MR_EXPIRES_AT)),
        registered_at=_ts(row[t.MR_REGISTERED_AT]),
    )


class SupabaseRepository:
    def __init__(
        self, client: httpx.AsyncClient, base_url: str, service_key: str, timeout_s: float
    ) -> None:
        self._client = client
        self._rest = f"{base_url}/rest/v1"
        self._storage = f"{base_url}/storage/v1"
        self._timeout = timeout_s
        headers = {"apikey": service_key, "Accept": "application/json"}
        # Legacy service_role keys are JWTs and go in Authorization as well; the
        # new opaque sb_secret_* keys must only be sent as `apikey`.
        if service_key.count(".") == 2:
            headers["Authorization"] = f"Bearer {service_key}"
        self._headers = headers

    # -- low level ------------------------------------------------------------------
    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        json: Any = None,
        prefer: str | None = None,
    ) -> httpx.Response:
        headers = dict(self._headers)
        if prefer:
            headers["Prefer"] = prefer
        try:
            return await self._client.request(
                method, url, params=params, json=json, headers=headers, timeout=self._timeout
            )
        except httpx.HTTPError:
            raise RepositoryError() from None

    async def _get_rows(self, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        resp = await self._request("GET", f"{self._rest}/{table}", params=params)
        if resp.status_code != 200:
            raise RepositoryError(resp.status_code)
        data = resp.json()
        if not isinstance(data, list):
            raise RepositoryError(resp.status_code)
        return [row for row in data if isinstance(row, dict)]

    @staticmethod
    def _db_message(resp: httpx.Response) -> str:
        try:
            body = resp.json()
        except ValueError:
            return ""
        msg = body.get("message") if isinstance(body, dict) else None
        return msg if isinstance(msg, str) else ""

    # -- MlRepository -----------------------------------------------------------------
    async def ping(self) -> bool:
        try:
            await self._get_rows(t.FEATURE_FLAG, {"select": t.FEATURE_FLAG_KEY, "limit": "1"})
        except RepositoryError:
            return False
        return True

    async def get_roles(self, user_id: str) -> frozenset[str]:
        rows = await self._get_rows(
            t.USER_ROLE,
            {"select": t.USER_ROLE_ROLE, t.USER_ROLE_USER_ID: f"eq.{_uuid(user_id)}"},
        )
        return frozenset(str(r[t.USER_ROLE_ROLE]) for r in rows if t.USER_ROLE_ROLE in r)

    async def flag_enabled(self, key: str) -> bool:
        rows = await self._get_rows(
            t.FEATURE_FLAG,
            {"select": t.FEATURE_FLAG_ENABLED, t.FEATURE_FLAG_KEY: f"eq.{key}"},
        )
        return bool(rows) and rows[0].get(t.FEATURE_FLAG_ENABLED) is True

    async def get_image_upload(self, image_upload_id: str) -> ImageUpload | None:
        rows = await self._get_rows(
            t.IMAGE_UPLOAD,
            {"select": _IMAGE_COLS, t.IMAGE_UPLOAD_ID: f"eq.{_uuid(image_upload_id)}"},
        )
        return _image(rows[0]) if rows else None

    async def children_with_active_dataset_consent(self, child_ids: set[str]) -> set[str]:
        if not child_ids:
            return set()
        ids = ",".join(sorted(_uuid(c) for c in child_ids))
        rows = await self._get_rows(
            t.CONSENT,
            {
                "select": t.CONSENT_CHILD_ID,
                t.CONSENT_CHILD_ID: f"in.({ids})",
                t.CONSENT_TYPE: f"eq.{t.CONSENT_TYPE_DATASET}",
                t.CONSENT_WITHDRAWN_AT: "is.null",
            },
        )
        return {str(r[t.CONSENT_CHILD_ID]) for r in rows if t.CONSENT_CHILD_ID in r}

    async def enqueue_review(
        self, image_upload_id: str, enqueued_by: str, required_reviews: int
    ) -> tuple[QueueItem, bool]:
        image_id = _uuid(image_upload_id)
        resp = await self._request(
            "POST",
            f"{self._rest}/{t.REVIEW_QUEUE}",
            params={"on_conflict": t.RQ_IMAGE_UPLOAD_ID, "select": _QUEUE_COLS},
            json={
                t.RQ_IMAGE_UPLOAD_ID: image_id,
                t.RQ_ENQUEUED_BY: _uuid(enqueued_by),
                t.RQ_REQUIRED_REVIEWS: required_reviews,
            },
            prefer="return=representation,resolution=ignore-duplicates",
        )
        if resp.status_code in (400, 403) and t.DB_ERR_NOT_ELIGIBLE in self._db_message(resp):
            raise NotEligibleError
        if resp.status_code not in (200, 201):
            raise RepositoryError(resp.status_code)
        rows = resp.json()
        if isinstance(rows, list) and rows:
            return _queue_item(rows[0]), True
        existing = await self._get_rows(
            t.REVIEW_QUEUE, {"select": _QUEUE_COLS, t.RQ_IMAGE_UPLOAD_ID: f"eq.{image_id}"}
        )
        if not existing:
            raise RepositoryError(resp.status_code)
        return _queue_item(existing[0]), False

    async def list_pending(
        self, limit: int, after: Cursor | None, now: datetime
    ) -> list[tuple[QueueItem, ImageUpload]]:
        embed_cols = ",".join(
            (
                t.IMAGE_UPLOAD_ID,
                t.IMAGE_UPLOAD_CHILD_ID,
                t.IMAGE_UPLOAD_STATUS,
                t.IMAGE_UPLOAD_EXPIRES_AT,
            )
        )
        params = {
            "select": f"{_QUEUE_COLS},{t.IMAGE_UPLOAD}!inner({embed_cols})",
            t.RQ_STATUS: f"eq.{t.RQ_STATUS_PENDING}",
            f"{t.IMAGE_UPLOAD}.{t.IMAGE_UPLOAD_STATUS}": f"eq.{t.IMAGE_STATUS_UPLOADED}",
            f"{t.IMAGE_UPLOAD}.{t.IMAGE_UPLOAD_EXPIRES_AT}": f'gt."{now.isoformat()}"',
            "order": f"{t.RQ_ENQUEUED_AT}.asc,{t.RQ_ID}.asc",
            "limit": str(limit),
        }
        if after is not None:
            ts, last_id = after[0].isoformat(), _uuid(after[1])
            params["or"] = (
                f'({t.RQ_ENQUEUED_AT}.gt."{ts}",'
                f'and({t.RQ_ENQUEUED_AT}.eq."{ts}",{t.RQ_ID}.gt.{last_id}))'
            )
        rows = await self._get_rows(t.REVIEW_QUEUE, params)
        out: list[tuple[QueueItem, ImageUpload]] = []
        for row in rows:
            embedded = row.get(t.IMAGE_UPLOAD)
            if isinstance(embedded, dict):
                out.append((_queue_item(row), _image(embedded)))
        return out

    async def get_queue_item(self, queue_id: str) -> QueueItem | None:
        rows = await self._get_rows(
            t.REVIEW_QUEUE, {"select": _QUEUE_COLS, t.RQ_ID: f"eq.{_uuid(queue_id)}"}
        )
        return _queue_item(rows[0]) if rows else None

    async def add_review(self, review: NewReview) -> tuple[ReviewRecord, QueueItem]:
        resp = await self._request(
            "POST",
            f"{self._rest}/{t.REVIEW_RESULT}",
            params={
                "select": ",".join(
                    (t.RR_ID, t.RR_QUEUE_ID, t.RR_GRADER, t.RR_DECISION, t.RR_CREATED_AT)
                )
            },
            json={
                t.RR_QUEUE_ID: _uuid(review.queue_id),
                t.RR_REVIEWER_ID: _uuid(review.reviewer_id),
                t.RR_GRADER: review.grader_pseudonym,
                t.RR_DECISION: review.decision,
                t.RR_IMAGE_WIDTH: review.image_width,
                t.RR_IMAGE_HEIGHT: review.image_height,
                t.RR_BOXES: review.boxes,
                t.RR_NOTES: review.notes,
            },
            prefer="return=representation",
        )
        if resp.status_code == 409:
            raise DuplicateError
        if resp.status_code in (400, 403):
            message = self._db_message(resp)
            if t.DB_ERR_QUEUE_CLOSED in message:
                raise QueueClosedError
            if t.DB_ERR_NOT_ELIGIBLE in message:
                raise NotEligibleError
        if resp.status_code != 201:
            raise RepositoryError(resp.status_code)
        rows = resp.json()
        if not isinstance(rows, list) or not rows:
            raise RepositoryError(resp.status_code)
        row = rows[0]
        record = ReviewRecord(
            id=str(row[t.RR_ID]),
            queue_id=str(row[t.RR_QUEUE_ID]),
            grader_pseudonym=str(row[t.RR_GRADER]),
            decision=str(row[t.RR_DECISION]),
            created_at=_ts(row[t.RR_CREATED_AT]),
        )
        item = await self.get_queue_item(review.queue_id)
        if item is None:
            raise RepositoryError()
        return record, item

    async def latest_model(self) -> ModelRecord | None:
        rows = await self._get_rows(
            t.MODEL_REGISTRY,
            {"select": _MODEL_COLS, "order": f"{t.MR_SEQUENCE}.desc", "limit": "1"},
        )
        return _model(rows[0]) if rows else None

    async def get_model_by_sha(self, manifest_sha256: str) -> ModelRecord | None:
        rows = await self._get_rows(
            t.MODEL_REGISTRY,
            {"select": _MODEL_COLS, t.MR_MANIFEST_SHA256: f"eq.{manifest_sha256}"},
        )
        return _model(rows[0]) if rows else None

    async def insert_model(self, model: NewModel) -> ModelRecord:
        body = {
            t.MR_MANIFEST_SHA256: model.manifest_sha256,
            t.MR_SEQUENCE: model.sequence,
            t.MR_SCHEMA_VERSION: model.schema_version,
            t.MR_CHANNEL: model.channel,
            t.MR_SIGNER_KEY_ID: model.signer_key_id,
            t.MR_MIN_APP_VERSION: model.min_app_version,
            t.MR_MODEL_VERSION: model.model_version,
            t.MR_MODEL_SHA256: model.model_sha256,
            t.MR_MODEL_LICENSE: model.model_license,
            t.MR_RULES_VERSION: model.rules_version,
            t.MR_RECOMMENDER_VERSION: model.recommender_version,
            t.MR_MANIFEST_CREATED_AT: model.manifest_created_at.isoformat(),
            t.MR_EXPIRES_AT: model.expires_at.isoformat() if model.expires_at else None,
            t.MR_MANIFEST_CANONICAL: model.manifest_canonical,
            t.MR_MANIFEST_SIG: model.manifest_sig,
            t.MR_REGISTERED_BY: _uuid(model.registered_by),
        }
        resp = await self._request(
            "POST",
            f"{self._rest}/{t.MODEL_REGISTRY}",
            params={"select": _MODEL_COLS},
            json=body,
            prefer="return=representation",
        )
        if resp.status_code == 409:
            raise DuplicateError
        if resp.status_code != 201:
            raise RepositoryError(resp.status_code)
        rows = resp.json()
        if not isinstance(rows, list) or not rows:
            raise RepositoryError(resp.status_code)
        return _model(rows[0])

    async def audit(
        self,
        actor_id: str | None,
        action: str,
        target_table: str | None,
        target_id: str | None,
        details: dict[str, str | int | bool | None],
    ) -> None:
        resp = await self._request(
            "POST",
            f"{self._rest}/rpc/{t.RPC_ML_AUDIT}",
            json={
                "p_actor": _uuid(actor_id) if actor_id else None,
                "p_action": action,
                "p_target_table": target_table,
                "p_target": _uuid(target_id) if target_id else None,
                "p_details": details,
            },
        )
        if resp.status_code not in (200, 204):
            raise RepositoryError(resp.status_code)

    async def fetch_photo(self, object_path: str, max_bytes: int) -> bytes:
        url = (
            f"{self._storage}/object/authenticated/{t.IMAGE_UPLOAD_BUCKET}/"
            f"{quote(object_path, safe='/')}"
        )
        try:
            async with self._client.stream(
                "GET", url, headers=self._headers, timeout=self._timeout
            ) as resp:
                if resp.status_code != 200:
                    raise RepositoryError(resp.status_code)
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf += chunk
                    if len(buf) > max_bytes:
                        raise ValueError("photo larger than the bucket limit")
                return bytes(buf)
        except httpx.HTTPError:
            raise RepositoryError() from None
