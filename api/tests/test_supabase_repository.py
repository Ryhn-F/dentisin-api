"""PostgREST implementation against a mocked Supabase (respx; no network)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
import respx
from app.repositories import tables as t
from app.repositories.base import (
    DuplicateError,
    NewModel,
    NewReview,
    NotEligibleError,
    QueueClosedError,
    RepositoryError,
)
from app.repositories.supabase import SupabaseRepository

from .conftest import ADMIN, CHILD, IMG, IMG_PATH

BASE = "https://testproj.supabase.co"
REST = f"{BASE}/rest/v1"
QID = "33333333-3333-4333-8333-333333333333"
TS = "2026-10-09T01:02:03.456789+00:00"

QUEUE_ROW = {
    "id": QID,
    "image_upload_id": IMG,
    "status": "pending",
    "required_reviews": 2,
    "review_count": 0,
    "enqueued_at": TS,
    "completed_at": None,
}
MODEL_ROW = {
    "id": QID,
    "manifest_sha256": "a" * 64,
    "sequence": 7,
    "schema_version": 1,
    "channel": "production",
    "signer_key_id": "0123456789abcdef",
    "min_app_version": "1.0.0",
    "model_version": "0.1.0",
    "model_sha256": "b" * 64,
    "model_license": "Apache-2.0",
    "rules_version": "2026.10.1",
    "recommender_version": "1.0.0",
    "manifest_created_at": "2026-10-08T00:00:00+00:00",
    "expires_at": None,
    "registered_at": TS,
}


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


def _repo(http: httpx.AsyncClient, key: str = "a.b.c") -> SupabaseRepository:
    return SupabaseRepository(http, BASE, key, 5.0)


pytestmark = pytest.mark.anyio


@respx.mock
async def test_headers_and_roles(http: httpx.AsyncClient) -> None:
    route = respx.get(f"{REST}/user_role").mock(
        return_value=httpx.Response(200, json=[{"role": "admin"}, {"role": "guardian"}, {}])
    )
    assert await _repo(http).get_roles(ADMIN) == frozenset({"admin", "guardian"})
    req = route.calls.last.request
    assert req.headers["apikey"] == "a.b.c"
    assert req.headers["authorization"] == "Bearer a.b.c"  # legacy JWT key
    assert req.url.params["user_id"] == f"eq.{ADMIN}"

    await _repo(http, key="sb_secret_opaque").get_roles(ADMIN)
    req = route.calls.last.request
    assert "authorization" not in req.headers  # new opaque keys only as apikey


@respx.mock
async def test_get_roles_rejects_non_uuid(http: httpx.AsyncClient) -> None:
    with pytest.raises(ValueError, match="UUID"):
        await _repo(http).get_roles("x,role.eq.admin")


@respx.mock
async def test_flag_ping_and_errors(http: httpx.AsyncClient) -> None:
    route = respx.get(f"{REST}/feature_flag")
    route.mock(return_value=httpx.Response(200, json=[{"enabled": True}]))
    assert await _repo(http).flag_enabled("dataset_contribution") is True
    assert await _repo(http).ping() is True
    route.mock(return_value=httpx.Response(200, json=[]))
    assert await _repo(http).flag_enabled("dataset_contribution") is False
    route.mock(return_value=httpx.Response(500, json={"message": "x"}))
    assert await _repo(http).ping() is False
    with pytest.raises(RepositoryError) as info:
        await _repo(http).flag_enabled("dataset_contribution")
    assert info.value.upstream_status == 500
    route.mock(return_value=httpx.Response(200, json={"not": "a list"}))
    with pytest.raises(RepositoryError):
        await _repo(http).flag_enabled("dataset_contribution")
    route.mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(RepositoryError):
        await _repo(http).flag_enabled("dataset_contribution")


@respx.mock
async def test_image_and_consent(http: httpx.AsyncClient) -> None:
    respx.get(f"{REST}/image_upload").mock(
        side_effect=[
            httpx.Response(
                200,
                json=[
                    {
                        "id": IMG,
                        "child_id": CHILD,
                        "status": "uploaded",
                        "expires_at": "2027-01-01T00:00:00",
                        "object_path": IMG_PATH,
                    }
                ],
            ),
            httpx.Response(200, json=[]),
        ]
    )
    image = await _repo(http).get_image_upload(IMG)
    assert image is not None and image.expires_at.tzinfo is not None
    assert IMG_PATH not in repr(image)
    assert await _repo(http).get_image_upload(IMG) is None

    consent = respx.get(f"{REST}/consent").mock(
        return_value=httpx.Response(200, json=[{"child_id": CHILD}])
    )
    assert await _repo(http).children_with_active_dataset_consent(set()) == set()
    assert await _repo(http).children_with_active_dataset_consent({CHILD}) == {CHILD}
    params = consent.calls.last.request.url.params
    assert params["consent_type"] == "eq.c" and params["withdrawn_at"] == "is.null"
    assert params["child_id"] == f"in.({CHILD})"


@respx.mock
async def test_enqueue_paths(http: httpx.AsyncClient) -> None:
    post = respx.post(f"{REST}/ml_review_queue")
    post.mock(return_value=httpx.Response(201, json=[QUEUE_ROW]))
    item, created = await _repo(http).enqueue_review(IMG, ADMIN, 2)
    assert created and item.id == QID
    req = post.calls.last.request
    assert "resolution=ignore-duplicates" in req.headers["prefer"]
    assert req.url.params["on_conflict"] == "image_upload_id"
    assert json.loads(req.content) == {
        "image_upload_id": IMG,
        "enqueued_by": ADMIN,
        "required_reviews": 2,
    }

    post.mock(return_value=httpx.Response(201, json=[]))
    get = respx.get(f"{REST}/ml_review_queue").mock(
        return_value=httpx.Response(200, json=[QUEUE_ROW])
    )
    item, created = await _repo(http).enqueue_review(IMG, ADMIN, 2)
    assert not created and item.id == QID
    get.mock(return_value=httpx.Response(200, json=[]))
    with pytest.raises(RepositoryError):
        await _repo(http).enqueue_review(IMG, ADMIN, 2)

    post.mock(
        return_value=httpx.Response(400, json={"code": "P0001", "message": t.DB_ERR_NOT_ELIGIBLE})
    )
    with pytest.raises(NotEligibleError):
        await _repo(http).enqueue_review(IMG, ADMIN, 2)
    post.mock(return_value=httpx.Response(400, text="not json"))
    with pytest.raises(RepositoryError):
        await _repo(http).enqueue_review(IMG, ADMIN, 2)


@respx.mock
async def test_list_pending_query(http: httpx.AsyncClient) -> None:
    row = dict(QUEUE_ROW)
    row["image_upload"] = {
        "id": IMG,
        "child_id": CHILD,
        "status": "uploaded",
        "expires_at": "2027-01-01T00:00:00+00:00",
    }
    route = respx.get(f"{REST}/ml_review_queue").mock(
        return_value=httpx.Response(200, json=[row, dict(QUEUE_ROW)])
    )
    now = datetime(2026, 10, 9, tzinfo=UTC)
    rows = await _repo(http).list_pending(10, None, now)
    assert len(rows) == 1 and rows[0][1].child_id == CHILD
    params = route.calls.last.request.url.params
    assert "image_upload!inner(" in params["select"] and "object_path" not in params["select"]
    assert params["image_upload.status"] == "eq.uploaded"
    assert params["status"] == "eq.pending"
    assert "or" not in params
    await _repo(http).list_pending(10, (now, QID), now)
    params = route.calls.last.request.url.params
    assert params["or"].startswith("(enqueued_at.gt.") and QID in params["or"]


@respx.mock
async def test_add_review_paths(http: httpx.AsyncClient) -> None:
    review = NewReview(QID, ADMIN, "drg-a1", "no_finding", 10, 10, [], None)
    post = respx.post(f"{REST}/ml_review_result")
    respx.get(f"{REST}/ml_review_queue").mock(return_value=httpx.Response(200, json=[QUEUE_ROW]))
    post.mock(
        return_value=httpx.Response(
            201,
            json=[
                {
                    "id": QID,
                    "queue_id": QID,
                    "grader_pseudonym": "drg-a1",
                    "decision": "no_finding",
                    "created_at": TS,
                }
            ],
        )
    )
    record, item = await _repo(http).add_review(review)
    assert record.grader_pseudonym == "drg-a1" and item.id == QID

    for status, body, exc in (
        (409, {"code": "23505"}, DuplicateError),
        (400, {"message": t.DB_ERR_QUEUE_CLOSED}, QueueClosedError),
        (400, {"message": t.DB_ERR_NOT_ELIGIBLE}, NotEligibleError),
        (400, {"message": "other"}, RepositoryError),
        (201, [], RepositoryError),
    ):
        post.mock(return_value=httpx.Response(status, json=body))
        with pytest.raises(exc):
            await _repo(http).add_review(review)


@respx.mock
async def test_model_registry_paths(http: httpx.AsyncClient) -> None:
    get = respx.get(f"{REST}/ml_model_registry")
    get.mock(return_value=httpx.Response(200, json=[MODEL_ROW]))
    latest = await _repo(http).latest_model()
    assert latest is not None and latest.sequence == 7
    assert get.calls.last.request.url.params["order"] == "sequence.desc"
    assert await _repo(http).get_model_by_sha("a" * 64) is not None
    get.mock(return_value=httpx.Response(200, json=[]))
    assert await _repo(http).latest_model() is None
    assert await _repo(http).get_model_by_sha("a" * 64) is None

    new = NewModel(
        "a" * 64,
        7,
        1,
        "production",
        "0123456789abcdef",
        "1.0.0",
        "0.1.0",
        "b" * 64,
        "Apache-2.0",
        "2026.10.1",
        "1.0.0",
        datetime(2026, 10, 8, tzinfo=UTC),
        None,
        "{}",
        "{}",
        ADMIN,
    )
    post = respx.post(f"{REST}/ml_model_registry")
    post.mock(return_value=httpx.Response(201, json=[MODEL_ROW]))
    assert (await _repo(http).insert_model(new)).id == QID
    assert json.loads(post.calls.last.request.content)["registered_by"] == ADMIN
    for status, body, exc in (
        (409, {}, DuplicateError),
        (500, {}, RepositoryError),
        (201, [], RepositoryError),
    ):
        post.mock(return_value=httpx.Response(status, json=body))
        with pytest.raises(exc):
            await _repo(http).insert_model(new)


@respx.mock
async def test_audit_rpc(http: httpx.AsyncClient) -> None:
    rpc = respx.post(f"{REST}/rpc/service_ml_audit").mock(return_value=httpx.Response(204))
    await _repo(http).audit(ADMIN, "ml_review_enqueued", "ml_review_queue", QID, {"n": 1})
    body = json.loads(rpc.calls.last.request.content)
    assert body == {
        "p_actor": ADMIN,
        "p_action": "ml_review_enqueued",
        "p_target_table": "ml_review_queue",
        "p_target": QID,
        "p_details": {"n": 1},
    }
    await _repo(http).audit(None, "x_y", None, None, {})
    rpc.mock(return_value=httpx.Response(500))
    with pytest.raises(RepositoryError):
        await _repo(http).audit(None, "x_y", None, None, {})


@respx.mock
async def test_fetch_photo(http: httpx.AsyncClient) -> None:
    url = f"{BASE}/storage/v1/object/authenticated/dataset_photos/{IMG_PATH}"
    route = respx.get(url).mock(return_value=httpx.Response(200, content=b"jpegbytes"))
    assert await _repo(http).fetch_photo(IMG_PATH, 100) == b"jpegbytes"
    assert route.calls.last.request.headers["apikey"] == "a.b.c"
    with pytest.raises(ValueError, match="larger"):
        await _repo(http).fetch_photo(IMG_PATH, 3)
    route.mock(return_value=httpx.Response(404))
    with pytest.raises(RepositoryError):
        await _repo(http).fetch_photo(IMG_PATH, 100)
    route.mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(RepositoryError):
        await _repo(http).fetch_photo(IMG_PATH, 100)


async def test_bad_timestamp_raises(http: httpx.AsyncClient) -> None:
    from app.repositories.supabase import _ts

    with pytest.raises(RepositoryError):
        _ts(None)
