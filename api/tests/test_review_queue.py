"""Review queue: RBAC, consent/flag gating, idempotency, validation, pagination."""

from __future__ import annotations

from typing import Any

import pytest
from app.repositories.base import ImageUpload, QueueItem
from app.repositories.memory import InMemoryRepository
from app.services.review_queue import decode_cursor
from fastapi.testclient import TestClient

from .conftest import (
    ADMIN,
    CHILD,
    GUARDIAN,
    IMG,
    IMG_EXPIRED,
    IMG_NO_CONSENT,
    IMG_WITHDRAWN,
    KID,
    NOROLE,
    RETIRED,
    TEEN,
    TokenFactory,
    auth,
    new_uuid,
)

Q = "/v1/ml/review-queue"


def _code(resp: Any) -> str:
    return str(resp.json()["error"]["code"])


def _review_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "grader_pseudonym": "drg-a1",
        "decision": "annotated",
        "image_width": 1080,
        "image_height": 1440,
        "boxes": [{"cls": "caries_permanent", "x1": 10, "y1": 20.5, "x2": 50, "y2": 70}],
        "notes": "lesi kecil di gigi depan",
    }
    body.update(overrides)
    return body


def _enqueue(client: TestClient, token: str, image: str = IMG, **extra: Any) -> Any:
    return client.post(Q, json={"image_upload_id": image, **extra}, headers=auth(token))


# --- RBAC matrix ---------------------------------------------------------------------

NON_ADMINS = [GUARDIAN, TEEN, RETIRED, KID, NOROLE]


@pytest.mark.parametrize("user", NON_ADMINS)
def test_rbac_non_admin_forbidden_everywhere(
    client: TestClient, make_token: TokenFactory, repo: InMemoryRepository, user: str
) -> None:
    token = make_token(user)
    admin = make_token(ADMIN)
    queue_id = _enqueue(client, admin).json()["item"]["id"]
    calls = [
        client.post(Q, json={"image_upload_id": IMG}, headers=auth(token)),
        client.get(Q, headers=auth(token)),
        client.post(f"{Q}/{queue_id}/review", json=_review_body(), headers=auth(token)),
        client.post(
            "/v1/ml/models", json={"manifest_json": "{}", "manifest_sig": "{}"}, headers=auth(token)
        ),
    ]
    assert [r.status_code for r in calls] == [403, 403, 403, 403]
    assert all(_code(r) == "forbidden" for r in calls)
    assert repo.reviews == {}


def test_rbac_admin_allowed(client: TestClient, make_token: TokenFactory) -> None:
    admin = make_token(ADMIN)
    resp = _enqueue(client, admin)
    assert resp.status_code == 201
    queue_id = resp.json()["item"]["id"]
    assert client.get(Q, headers=auth(admin)).status_code == 200
    review = client.post(f"{Q}/{queue_id}/review", json=_review_body(), headers=auth(admin))
    assert review.status_code == 201


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", Q),
        ("get", Q),
        ("post", f"{Q}/00000000-0000-4000-8000-000000000000/review"),
        ("post", "/v1/ml/models"),
    ],
)
def test_rbac_unauthenticated_401(client: TestClient, method: str, path: str) -> None:
    resp = getattr(client, method)(path, json={}) if method == "post" else client.get(path)
    assert resp.status_code == 401


# --- enqueue gating & idempotency -------------------------------------------------------


def test_enqueue_is_idempotent(
    client: TestClient, make_token: TokenFactory, repo: InMemoryRepository
) -> None:
    admin = make_token(ADMIN)
    first = _enqueue(client, admin)
    second = _enqueue(client, admin, required_reviews=3)
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["created"] is True and second.json()["created"] is False
    assert first.json()["item"]["id"] == second.json()["item"]["id"]
    assert second.json()["item"]["required_reviews"] == 2  # unchanged by the replay
    assert len(repo.queue) == 1
    assert [a.action for a in repo.audit_log].count("ml_review_enqueued") == 1


def test_enqueue_response_has_no_storage_path(client: TestClient, make_token: TokenFactory) -> None:
    body = _enqueue(client, make_token(ADMIN)).text
    assert "dataset_photos" not in body and ".jpg" not in body and GUARDIAN not in body
    assert CHILD not in body


def test_enqueue_flag_off(
    client: TestClient, make_token: TokenFactory, repo: InMemoryRepository
) -> None:
    repo.flags["dataset_contribution"] = False
    resp = _enqueue(client, make_token(ADMIN))
    assert resp.status_code == 409
    assert _code(resp) == "dataset_contribution_disabled"
    # Flag is checked first: an unknown image reveals nothing either.
    resp = _enqueue(client, make_token(ADMIN), image=new_uuid())
    assert _code(resp) == "dataset_contribution_disabled"
    assert repo.queue == {}


def test_enqueue_flag_missing_counts_as_off(
    client: TestClient, make_token: TokenFactory, repo: InMemoryRepository
) -> None:
    repo.flags = {}
    assert _code(_enqueue(client, make_token(ADMIN))) == "dataset_contribution_disabled"


@pytest.mark.parametrize(
    ("image", "status", "code"),
    [
        (IMG_NO_CONSENT, 409, "consent_inactive"),
        (IMG_WITHDRAWN, 409, "image_not_eligible"),
        (IMG_EXPIRED, 409, "image_not_eligible"),
        ("99999999-9999-4999-8999-999999999999", 404, "image_upload_not_found"),
    ],
)
def test_enqueue_gating(
    client: TestClient,
    make_token: TokenFactory,
    repo: InMemoryRepository,
    image: str,
    status: int,
    code: str,
) -> None:
    resp = _enqueue(client, make_token(ADMIN), image=image)
    assert resp.status_code == status
    assert _code(resp) == code
    assert repo.queue == {}


def test_enqueue_db_guard_race_maps_to_409(
    client: TestClient, make_token: TokenFactory, repo: InMemoryRepository
) -> None:
    """Consent withdrawn between the API check and the INSERT: the DB guard
    trigger (mirrored by the fake) refuses and the API answers 409."""

    original = repo.enqueue_review

    async def racing(
        image_upload_id: str, enqueued_by: str, required_reviews: int
    ) -> tuple[QueueItem, bool]:
        repo.dataset_consent[CHILD] = False
        return await original(image_upload_id, enqueued_by, required_reviews)

    repo.enqueue_review = racing  # type: ignore[method-assign]
    resp = _enqueue(client, make_token(ADMIN))
    assert resp.status_code == 409
    assert _code(resp) == "image_not_eligible"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"image_upload_id": "not-a-uuid"},
        {"image_upload_id": IMG, "extra": 1},
        {"image_upload_id": IMG, "required_reviews": 0},
        {"image_upload_id": IMG, "required_reviews": 6},
        {"image_upload_id": IMG, "required_reviews": "2"},
        {"image_upload_id": IMG, "object_path": "x/y/z.jpg"},
        {"image_upload_id": IMG, "photo": "/9j/4AAQSkZJRg=="},
    ],
)
def test_enqueue_validation(client: TestClient, make_token: TokenFactory, body: Any) -> None:
    resp = client.post(Q, json=body, headers=auth(make_token(ADMIN)))
    assert resp.status_code == 422
    payload = resp.json()["error"]
    assert payload["code"] == "validation_error"
    # Offending input values are never reflected back.
    assert "x/y/z.jpg" not in resp.text and "4AAQ" not in resp.text


def test_enqueue_rejects_non_json(client: TestClient, make_token: TokenFactory) -> None:
    resp = client.post(
        Q,
        content=b"image_upload_id=" + IMG.encode(),
        headers={**auth(make_token(ADMIN)), "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 422


# --- listing --------------------------------------------------------------------------


def test_list_pending_excludes_ineligible_and_paginates(
    client: TestClient, make_token: TokenFactory, repo: InMemoryRepository
) -> None:
    admin = make_token(ADMIN)
    future = repo.images[IMG].expires_at
    ids = []
    for _ in range(5):
        image_id = new_uuid()
        repo.images[image_id] = ImageUpload(
            image_id, CHILD, "uploaded", future, f"{GUARDIAN}/{CHILD}/{image_id}.jpg"
        )
        ids.append(_enqueue(client, admin, image=image_id).json()["item"]["id"])
    # One item whose photo is withdrawn later: excluded from the list.
    withdrawn_image = repo.images[repo.queue[ids[0]].image_upload_id]
    repo.images[withdrawn_image.id] = ImageUpload(
        withdrawn_image.id, CHILD, "withdrawn", future, withdrawn_image.object_path
    )

    page1 = client.get(Q, params={"limit": 2}, headers=auth(admin)).json()
    assert len(page1["items"]) == 2 and page1["next_cursor"]
    page2 = client.get(
        Q, params={"limit": 2, "cursor": page1["next_cursor"]}, headers=auth(admin)
    ).json()
    page3 = client.get(
        Q, params={"limit": 2, "cursor": page2["next_cursor"]}, headers=auth(admin)
    ).json()
    seen = [i["id"] for i in page1["items"] + page2["items"] + page3["items"]]
    assert seen == ids[1:]
    assert page3["next_cursor"] is None
    assert any(a.action == "ml_review_queue_listed" for a in repo.audit_log)


def test_list_filters_children_without_active_consent(
    client: TestClient, make_token: TokenFactory, repo: InMemoryRepository
) -> None:
    admin = make_token(ADMIN)
    _enqueue(client, admin)
    repo.dataset_consent[CHILD] = False  # withdrawn after enqueue
    assert client.get(Q, headers=auth(admin)).json()["items"] == []


def test_list_flag_off(
    client: TestClient, make_token: TokenFactory, repo: InMemoryRepository
) -> None:
    repo.flags["dataset_contribution"] = False
    resp = client.get(Q, headers=auth(make_token(ADMIN)))
    assert resp.status_code == 409 and _code(resp) == "dataset_contribution_disabled"


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 101}, {"limit": "x"}, {"cursor": "!!!"}, {"cursor": "a" * 201}],
)
def test_list_validation(client: TestClient, make_token: TokenFactory, params: Any) -> None:
    resp = client.get(Q, params=params, headers=auth(make_token(ADMIN)))
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "cursor",
    ["bm90LWEtY3Vyc29y", "MjAyNi0xMC0wOVQwMDowMDowMHxub3QtYS11dWlk", "MjAyNi0xMC0wOXx4"],
)
def test_bad_cursor_is_422(client: TestClient, make_token: TokenFactory, cursor: str) -> None:
    resp = client.get(Q, params={"cursor": cursor}, headers=auth(make_token(ADMIN)))
    assert resp.status_code == 422
    assert _code(resp) == "invalid_cursor"


def test_naive_timestamp_cursor_rejected() -> None:
    import base64

    raw = f"2026-10-09T00:00:00|{IMG}".encode()
    with pytest.raises(Exception, match="invalid_cursor"):
        decode_cursor(base64.urlsafe_b64encode(raw).decode())


# --- review submission --------------------------------------------------------------------


def _queued(client: TestClient, admin: str, **extra: Any) -> str:
    return str(_enqueue(client, admin, **extra).json()["item"]["id"])


def test_review_completes_after_required_reviews(
    client: TestClient, make_token: TokenFactory, repo: InMemoryRepository
) -> None:
    admin = make_token(ADMIN)
    queue_id = _queued(client, admin, required_reviews=2)
    r1 = client.post(f"{Q}/{queue_id}/review", json=_review_body(), headers=auth(admin))
    assert r1.status_code == 201
    assert r1.json()["queue_status"] == "pending" and r1.json()["review_count"] == 1
    r2 = client.post(
        f"{Q}/{queue_id}/review",
        json=_review_body(grader_pseudonym="drg-b2", decision="no_finding", boxes=[]),
        headers=auth(admin),
    )
    assert r2.status_code == 201
    assert r2.json()["queue_status"] == "completed" and r2.json()["review_count"] == 2
    # closed item: a third grader is refused
    r3 = client.post(
        f"{Q}/{queue_id}/review", json=_review_body(grader_pseudonym="drg-c3"), headers=auth(admin)
    )
    assert r3.status_code == 409 and _code(r3) == "queue_item_closed"
    stored = [rev for rev, _ in repo.reviews.values()]
    assert stored[0].boxes == [
        {"cls": "caries_permanent", "x1": 10.0, "y1": 20.5, "x2": 50.0, "y2": 70.0}
    ]
    assert stored[0].reviewer_id == ADMIN
    # completed items no longer listed
    assert client.get(Q, headers=auth(admin)).json()["items"] == []


def test_review_accepts_both_dentition_classes(
    client: TestClient, make_token: TokenFactory
) -> None:
    admin = make_token(ADMIN)
    queue_id = _queued(client, admin, required_reviews=2)
    boxes = [
        {"cls": "caries_permanent", "x1": 10, "y1": 20, "x2": 50, "y2": 70},
        {"cls": "caries_primary", "x1": 60, "y1": 20, "x2": 90, "y2": 70},
    ]
    resp = client.post(
        f"{Q}/{queue_id}/review", json=_review_body(boxes=boxes), headers=auth(admin)
    )
    assert resp.status_code == 201
    assert set(resp.json()).isdisjoint({"area", "rel_area", "severity"})


def test_same_grader_twice_conflict(client: TestClient, make_token: TokenFactory) -> None:
    admin = make_token(ADMIN)
    queue_id = _queued(client, admin, required_reviews=3)
    assert (
        client.post(f"{Q}/{queue_id}/review", json=_review_body(), headers=auth(admin)).status_code
        == 201
    )
    again = client.post(f"{Q}/{queue_id}/review", json=_review_body(), headers=auth(admin))
    assert again.status_code == 409 and _code(again) == "already_reviewed"


def test_review_unknown_queue_item(client: TestClient, make_token: TokenFactory) -> None:
    resp = client.post(
        f"{Q}/{new_uuid()}/review", json=_review_body(), headers=auth(make_token(ADMIN))
    )
    assert resp.status_code == 404 and _code(resp) == "queue_item_not_found"


def test_review_bad_queue_id(client: TestClient, make_token: TokenFactory) -> None:
    resp = client.post(
        f"{Q}/not-a-uuid/review", json=_review_body(), headers=auth(make_token(ADMIN))
    )
    assert resp.status_code == 422


def test_review_after_consent_withdrawn(
    client: TestClient, make_token: TokenFactory, repo: InMemoryRepository
) -> None:
    admin = make_token(ADMIN)
    queue_id = _queued(client, admin)
    repo.dataset_consent[CHILD] = False
    resp = client.post(f"{Q}/{queue_id}/review", json=_review_body(), headers=auth(admin))
    assert resp.status_code == 409 and _code(resp) == "consent_inactive"
    assert repo.reviews == {}


def test_review_after_flag_off(
    client: TestClient, make_token: TokenFactory, repo: InMemoryRepository
) -> None:
    admin = make_token(ADMIN)
    queue_id = _queued(client, admin)
    repo.flags["dataset_contribution"] = False
    resp = client.post(f"{Q}/{queue_id}/review", json=_review_body(), headers=auth(admin))
    assert resp.status_code == 409 and _code(resp) == "dataset_contribution_disabled"


def test_review_db_guard_errors_mapped(
    client: TestClient, make_token: TokenFactory, repo: InMemoryRepository
) -> None:
    from app.repositories.base import NotEligibleError, QueueClosedError

    admin = make_token(ADMIN)
    queue_id = _queued(client, admin)
    for exc, code in (
        (QueueClosedError, "queue_item_closed"),
        (NotEligibleError, "image_not_eligible"),
    ):

        async def boom(review: Any, _exc: type[Exception] = exc) -> Any:
            raise _exc

        repo.add_review = boom  # type: ignore[method-assign]
        resp = client.post(f"{Q}/{queue_id}/review", json=_review_body(), headers=auth(admin))
        assert resp.status_code == 409 and _code(resp) == code


BAD_REVIEWS: list[tuple[dict[str, Any], str]] = [
    ({"decision": "diagnosed"}, "unknown decision"),
    ({"grader_pseudonym": "Dr. Budi Santoso"}, "real-name-like grader"),
    ({"grader_pseudonym": "x"}, "too short grader"),
    ({"boxes": [{"cls": "tooth", "x1": 1, "y1": 1, "x2": 5, "y2": 5}]}, "unknown class"),
    (
        {"boxes": [{"cls": "caries_cavitated", "x1": 1, "y1": 1, "x2": 5, "y2": 5}]},
        "legacy cavitated",
    ),
    (
        {"boxes": [{"cls": "caries_noncavitated", "x1": 1, "y1": 1, "x2": 5, "y2": 5}]},
        "legacy noncav",
    ),
    (
        {"boxes": [{"cls": "caries_primary", "x1": 1, "y1": 1, "x2": 5, "y2": 5, "rel_area": 0.1}]},
        "box-size-derived field",
    ),
    ({"boxes": [{"cls": "caries_permanent", "x1": 50, "y1": 1, "x2": 10, "y2": 5}]}, "x1>x2"),
    ({"boxes": [{"cls": "caries_permanent", "x1": 1, "y1": 1, "x2": 1.5, "y2": 5}]}, "<1px"),
    ({"boxes": [{"cls": "caries_permanent", "x1": 1, "y1": 1, "x2": 2000, "y2": 5}]}, "outside"),
    ({"boxes": [{"cls": "caries_permanent", "x1": -1, "y1": 1, "x2": 5, "y2": 5}]}, "negative"),
    ({"boxes": [{"cls": "caries_permanent", "x1": 1, "y1": 1, "x2": 5, "y2": 5, "s": 1}]}, "extra"),
    ({"boxes": [{"cls": "caries_permanent", "x1": "1", "y1": 1, "x2": 5, "y2": 5}]}, "str coord"),
    ({"boxes": []}, "annotated without boxes"),
    ({"decision": "no_finding"}, "boxes with no_finding"),
    ({"notes": "x" * 501}, "notes too long"),
    ({"notes": "bad\x00char"}, "control char"),
    ({"image_width": 0}, "zero width"),
    ({"image_height": 20001}, "too tall"),
    ({"reviewer_id": ADMIN}, "unknown field"),
    (
        {"boxes": [{"cls": "caries_permanent", "x1": 1, "y1": 1, "x2": 5, "y2": 5}] * 101},
        "too many boxes",
    ),
]


@pytest.mark.parametrize(("override", "label"), BAD_REVIEWS)
def test_review_validation(
    client: TestClient,
    make_token: TokenFactory,
    repo: InMemoryRepository,
    override: dict[str, Any],
    label: str,
) -> None:
    admin = make_token(ADMIN)
    queue_id = _queued(client, admin)
    resp = client.post(f"{Q}/{queue_id}/review", json=_review_body(**override), headers=auth(admin))
    assert resp.status_code == 422, label
    assert repo.reviews == {}


def test_review_nan_rejected(client: TestClient, make_token: TokenFactory) -> None:
    admin = make_token(ADMIN)
    queue_id = _queued(client, admin)
    raw = (
        '{"grader_pseudonym":"drg-a1","decision":"annotated","image_width":100,'
        '"image_height":100,"boxes":[{"cls":"caries_permanent","x1":NaN,"y1":1,"x2":5,"y2":5}]}'
    )
    resp = client.post(
        f"{Q}/{queue_id}/review",
        content=raw.encode(),
        headers={**auth(admin), "Content-Type": "application/json"},
    )
    assert resp.status_code == 422
