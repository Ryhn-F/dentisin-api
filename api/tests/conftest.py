"""Shared fixtures. No real network: Supabase is replaced by InMemoryRepository
(or respx for the PostgREST implementation) and the JWKS by a local fetcher."""

from __future__ import annotations

import base64
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jwt
import pytest
from app.main import create_app
from app.repositories.base import ImageUpload
from app.repositories.memory import InMemoryRepository
from app.services.manifest_verify import JsonValue, canonicalize, key_id_of
from app.settings import Settings
from cryptography.hazmat.primitives.asymmetric.ec import SECP256R1, EllipticCurvePrivateKey
from cryptography.hazmat.primitives.asymmetric.ec import generate_private_key as ec_generate
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jwt.algorithms import ECAlgorithm

SUPABASE_URL = "https://testproj.supabase.co"
ISSUER = f"{SUPABASE_URL}/auth/v1"
HS_SECRET = "test-only-hs256-secret-with-at-least-32-bytes!!"
SERVICE_KEY = "test-service-role-key"
VECTORS_DIR = Path(__file__).resolve().parents[2] / "tools" / "manifest" / "vectors"

ADMIN = "00000000-0000-4000-8000-0000000000a1"
GUARDIAN = "00000000-0000-4000-8000-0000000000b1"
TEEN = "00000000-0000-4000-8000-0000000000c1"  # teen accounts hold the guardian role
RETIRED = "00000000-0000-4000-8000-0000000000d1"  # only retired role values (ADR 018)
KID = "00000000-0000-4000-8000-0000000000f1"  # child account (role child)
NOROLE = "00000000-0000-4000-8000-0000000000e1"
CHILD = "11111111-1111-4111-8111-111111111111"
CHILD_NO_CONSENT = "22222222-2222-4222-8222-222222222222"
IMG = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
IMG_NO_CONSENT = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
IMG_WITHDRAWN = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
IMG_EXPIRED = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
IMG_PATH = f"{GUARDIAN}/{CHILD}/{IMG}.jpg"

_ENV_KEYS = [
    k for k in Settings.model_fields if k.startswith(("SUPABASE_", "API_", "MANIFEST_", "ENABLE_"))
]


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let the developer's environment leak into tests."""
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@dataclass(frozen=True)
class SigningKeys:
    ec_private: EllipticCurvePrivateKey
    ec_kid: str
    ed_private: Ed25519PrivateKey
    ed_public_b64: str


@pytest.fixture(scope="session")
def keys() -> SigningKeys:
    ed = Ed25519PrivateKey.generate()
    raw = ed.public_key().public_bytes_raw()
    return SigningKeys(
        ec_private=ec_generate(SECP256R1()),
        ec_kid="test-ec-kid-1",
        ed_private=ed,
        ed_public_b64=base64.b64encode(raw).decode("ascii"),
    )


def jwks_doc(keys: SigningKeys) -> dict[str, Any]:
    jwk: dict[str, Any] = ECAlgorithm.to_jwk(keys.ec_private.public_key(), as_dict=True)
    jwk.update({"kid": keys.ec_kid, "alg": "ES256", "use": "sig"})
    return {"keys": [jwk]}


TokenFactory = Callable[..., str]


@pytest.fixture
def make_token(keys: SigningKeys) -> TokenFactory:
    def _make(
        sub: str = ADMIN,
        *,
        alg: str = "HS256",
        exp_delta: int = 600,
        aud: str | None = "authenticated",
        iss: str | None = ISSUER,
        role: str | None = "authenticated",
        app_metadata: dict[str, Any] | None = None,
        user_metadata: dict[str, Any] | None = None,
        key: Any = None,
        kid: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {"sub": sub, "iat": now, "exp": now + exp_delta}
        if aud is not None:
            claims["aud"] = aud
        if iss is not None:
            claims["iss"] = iss
        if role is not None:
            claims["role"] = role
        if app_metadata is not None:
            claims["app_metadata"] = app_metadata
        if user_metadata is not None:
            claims["user_metadata"] = user_metadata
        claims.update(extra or {})
        headers: dict[str, Any] = {}
        if alg == "HS256":
            signing_key = key if key is not None else HS_SECRET
        else:
            signing_key = key if key is not None else keys.ec_private
            headers["kid"] = kid if kid is not None else keys.ec_kid
        return jwt.encode(claims, signing_key, algorithm=alg, headers=headers)

    return _make


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "SUPABASE_URL": SUPABASE_URL,
        "SUPABASE_SERVICE_ROLE_KEY": SERVICE_KEY,
        "SUPABASE_JWT_SECRET": HS_SECRET,
        "API_RATE_LIMIT_PER_MINUTE": 10_000,
        "API_RATE_LIMIT_BURST": 10_000,
    }
    base.update(overrides)
    return Settings(**base)


def seeded_repo() -> InMemoryRepository:
    future = datetime.now(UTC) + timedelta(days=30)
    repo = InMemoryRepository()
    repo.roles = {
        ADMIN: {"admin", "guardian"},
        GUARDIAN: {"guardian"},
        TEEN: {"guardian"},
        RETIRED: {"kader", "facility_viewer"},
        KID: {"child"},
    }
    repo.flags = {"dataset_contribution": True}
    repo.dataset_consent = {CHILD: True, CHILD_NO_CONSENT: False}
    repo.images = {
        IMG: ImageUpload(IMG, CHILD, "uploaded", future, IMG_PATH),
        IMG_NO_CONSENT: ImageUpload(
            IMG_NO_CONSENT,
            CHILD_NO_CONSENT,
            "uploaded",
            future,
            f"{GUARDIAN}/{CHILD_NO_CONSENT}/{IMG_NO_CONSENT}.jpg",
        ),
        IMG_WITHDRAWN: ImageUpload(
            IMG_WITHDRAWN, CHILD, "withdrawn", future, f"{GUARDIAN}/{CHILD}/{IMG_WITHDRAWN}.jpg"
        ),
        IMG_EXPIRED: ImageUpload(
            IMG_EXPIRED,
            CHILD,
            "uploaded",
            datetime.now(UTC) - timedelta(days=1),
            f"{GUARDIAN}/{CHILD}/{IMG_EXPIRED}.jpg",
        ),
    }
    repo.photos = {IMG_PATH: b"\xff\xd8\xff\xe0fakejpeg"}
    return repo


@pytest.fixture
def repo() -> InMemoryRepository:
    return seeded_repo()


@pytest.fixture
def settings(keys: SigningKeys) -> Settings:
    return make_settings(MANIFEST_PUBLIC_KEY_B64=keys.ed_public_b64)


AppFactory = Callable[..., FastAPI]


@pytest.fixture
def app_factory(keys: SigningKeys, repo: InMemoryRepository) -> AppFactory:
    def _factory(settings: Settings | None = None, **kwargs: Any) -> FastAPI:
        async def fetch() -> dict[str, Any]:
            return jwks_doc(keys)

        kwargs.setdefault("repo", repo)
        kwargs.setdefault("jwks_fetcher", fetch)
        return create_app(
            settings=settings or make_settings(MANIFEST_PUBLIC_KEY_B64=keys.ed_public_b64),
            configure_logs=False,
            **kwargs,
        )

    return _factory


@pytest.fixture
def client(app_factory: AppFactory, settings: Settings) -> Iterator[TestClient]:
    with TestClient(app_factory(settings)) as c:
        yield c


def sign_manifest(
    doc: dict[str, JsonValue], private_key: Ed25519PrivateKey, key_id: str | None = None
) -> tuple[str, str]:
    """TEST-ONLY signer (the server never signs)."""
    raw = canonicalize(doc)
    kid = key_id or key_id_of(private_key.public_key().public_bytes_raw())
    sig_doc: dict[str, JsonValue] = {
        "alg": "Ed25519",
        "key_id": kid,
        "sig": base64.b64encode(private_key.sign(raw)).decode("ascii"),
    }
    return raw.decode("ascii"), canonicalize(sig_doc).decode("ascii")


def manifest_doc(key_id: str, sequence: int = 1, **overrides: JsonValue) -> dict[str, JsonValue]:
    base = "https://testproj.supabase.co/storage/v1/object/public/manifests"
    h = "a" * 64
    doc: dict[str, JsonValue] = {
        "schema_version": 1,
        "sequence": sequence,
        "created_at": "2026-10-08T00:00:00Z",
        "min_app_version": "1.0.0",
        "signer_key_id": key_id,
        "channel": "production",
        "model": {
            "version": "0.1.0",
            "url": f"{base}/model/0.1.0/model.tflite",
            "sha256": h,
            "size_bytes": 4_000_000,
            "labels_url": f"{base}/model/0.1.0/labels.json",
            "labels_sha256": h,
            "labels_size_bytes": 1200,
            "license": "Apache-2.0",
        },
        "rules": {
            "version": "2026.10.1",
            "url": f"{base}/rules/2026.10.1/rules.json",
            "sha256": h,
            "size_bytes": 8000,
        },
        "recommender": {
            "version": "1.0.0",
            "url": f"{base}/recommender/1.0.0/rules.json",
            "sha256": h,
            "size_bytes": 3000,
        },
    }
    doc.update(overrides)
    return doc


def new_uuid() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
