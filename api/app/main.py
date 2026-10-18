"""DentisAI ML/AI API (ADR 005/009). FastAPI is used ONLY for ML & AI
integration: dentist review queue, signed model/rules registry, optional QA
inference. Auth and CRUD stay in Supabase.

Run: ``uv run --env-file ../.env uvicorn app.main:app --no-access-log``
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .auth import JwksCache, JwksFetcher, TokenVerifier, http_jwks_fetcher
from .deps import AppState
from .errors import install_error_handlers
from .logging_setup import configure_logging
from .middleware import (
    BodySizeLimitMiddleware,
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from .ratelimit import TokenBucketLimiter
from .repositories.base import MlRepository
from .repositories.supabase import SupabaseRepository
from .routers import health, infer, models, review_queue, screening
from .services.detector import Detector, load_detector
from .services.manifest_verify import TrustStore
from .services.quality import QualityGate, load_reference_quality_gate
from .services.screening_rules import (
    RulesUnavailableError,
    ScreeningRules,
    load_screening_rules,
)
from .settings import Settings, get_settings

DESCRIPTION = """\
**DentisAI ML/AI API.** Hanya untuk ML & integrasi AI (ADR 005/009):
antrean review dokter gigi atas foto berizin, registri versi model/aturan
bertanda tangan, inferensi awan opsional untuk QA, dan pipeline skrining
referensi (`/v1/screening`, mati secara default). **Tidak ada CRUD dan
tidak ada auth sendiri** — keduanya di Supabase. Semua keluaran adalah
*indikasi skrining*, bukan diagnosis; selama aturan berstatus draf
(`rules_status != approved`) keluaran tidak boleh ditampilkan sebagai tervalidasi klinis.
"""


def _trust_store(settings: Settings) -> TrustStore | None:
    if not settings.MANIFEST_PUBLIC_KEY_B64:
        return None
    try:
        return TrustStore.from_b64(
            settings.MANIFEST_PUBLIC_KEY_B64, settings.MANIFEST_REVOKED_KEY_IDS
        )
    except ValueError:
        return None  # misconfigured key -> registration answers 503, never crashes


def _model_dir(settings: Settings) -> Path | None:
    return Path(settings.DETECTOR_MODEL_DIR) if settings.DETECTOR_MODEL_DIR else None


@lru_cache(maxsize=1)
def _screening_rules() -> ScreeningRules | None:
    """Loaded once per process (immutable files; ~0.3 s incl. the release gates)."""
    try:
        return load_screening_rules()
    except RulesUnavailableError:
        return None  # screening routes answer 503 rules_unavailable; nothing else breaks


def create_app(
    settings: Settings | None = None,
    repo: MlRepository | None = None,
    detector: Detector | None = None,
    jwks_fetcher: JwksFetcher | None = None,
    configure_logs: bool = True,
    screening_rules: ScreeningRules | bool | None = True,
    quality_gate: QualityGate | None = None,
) -> FastAPI:
    """``screening_rules``: True = load the shipped rules (default); a
    ``ScreeningRules`` instance = injected (tests: params profiles); None/False =
    unavailable. ``quality_gate``: None = load the ml/detection reference gate."""
    settings = settings or get_settings()
    if configure_logs:
        configure_logging(settings.API_LOG_LEVEL)

    http_client: httpx.AsyncClient | None = None
    if repo is None or (jwks_fetcher is None and settings.API_JWKS_ENABLED):
        http_client = httpx.AsyncClient(follow_redirects=False)
    if repo is None and http_client is not None:
        repo = SupabaseRepository(
            http_client,
            settings.SUPABASE_URL,
            settings.SUPABASE_SERVICE_ROLE_KEY.get_secret_value(),
            settings.API_UPSTREAM_TIMEOUT_SECONDS,
        )
    if jwks_fetcher is None and http_client is not None:
        jwks_fetcher = http_jwks_fetcher(http_client, settings)
    jwks = JwksCache(jwks_fetcher, settings.API_JWKS_CACHE_SECONDS) if jwks_fetcher else None

    if repo is None:  # pragma: no cover - unreachable, keeps the type checker honest
        raise RuntimeError("repository not configured")
    state = AppState(
        settings=settings,
        repo=repo,
        verifier=TokenVerifier(settings, jwks),
        user_limiter=TokenBucketLimiter(
            settings.API_RATE_LIMIT_PER_MINUTE, settings.API_RATE_LIMIT_BURST
        ),
        detector=detector or load_detector(_model_dir(settings)),
        trust_store=_trust_store(settings),
        http_client=http_client,
        screening=_screening_rules() if screening_rules is True else (screening_rules or None),
        quality_gate=quality_gate or load_reference_quality_gate(),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if http_client is not None:
                await http_client.aclose()

    docs = settings.API_DOCS_ENABLED
    app = FastAPI(
        title="DentisAI ML/AI API",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs else None,
        swagger_ui_oauth2_redirect_url=None,
    )
    app.state.dentisai = state
    install_error_handlers(app)

    for router in (health.router, review_queue.router, models.router, infer.router):
        app.include_router(router)
    screening.install(app)

    # Middleware: added last = outermost.
    app.add_middleware(
        RateLimitMiddleware,
        limiter=TokenBucketLimiter(
            settings.API_RATE_LIMIT_PER_MINUTE, settings.API_RATE_LIMIT_BURST
        ),
        trust_forwarded_for=settings.API_TRUST_FORWARDED_FOR,
    )
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_bytes=settings.API_MAX_BODY_BYTES,
        path_limits={screening.PHOTO_PATH: settings.API_MAX_PHOTO_BODY_BYTES},
    )
    if settings.API_ALLOWED_ORIGINS:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.API_ALLOWED_ORIGINS,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
            allow_credentials=False,
            max_age=600,
        )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)
    return app


app = create_app()
