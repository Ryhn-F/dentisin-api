"""Runtime settings, read from the environment only (pydantic-settings).

Secrets are ``SecretStr`` so they never appear in ``repr()``/logs. Nothing here
has a default secret value; a missing value makes ``/readyz`` report not-ready
instead of crashing at import time (the OpenAPI export must work without env).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None,  # never auto-read a .env; use `uv run --env-file ../.env ...` in dev
        extra="ignore",
        case_sensitive=True,
        frozen=True,
    )

    # --- Supabase (names shared with backend/.env.example) ---------------------
    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: SecretStr = SecretStr("")
    SUPABASE_SERVICE_ROLE_KEY: SecretStr = SecretStr("")
    # Legacy HS256 shared JWT secret. Empty -> HS256 tokens are rejected.
    SUPABASE_JWT_SECRET: SecretStr = SecretStr("")

    # --- Feature switches -------------------------------------------------------
    ENABLE_CLOUD_INFER: bool = False
    # /v1/screening/* (medical module, ADR 002 option 3): default OFF -> every
    # screening route answers 404, indistinguishable from a missing route.
    ENABLE_SCREENING_API: bool = False
    # Comma-separated exact origins. Empty (default) = no CORS headers at all.
    API_ALLOWED_ORIGINS: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- API behaviour (non-secret; defaults are the documented limits) ---------
    API_ENV: Literal["development", "production"] = "development"
    # Asymmetric Supabase JWT signing keys via <SUPABASE_URL>/auth/v1/.well-known/jwks.json
    API_JWKS_ENABLED: bool = True
    API_JWKS_CACHE_SECONDS: int = Field(default=600, ge=30, le=86_400)
    API_JWT_LEEWAY_SECONDS: int = Field(default=30, ge=0, le=300)
    # Where app roles come from. "table" = public.user_role (current schema; the
    # source of truth). "app_metadata" = JWT app_metadata.roles/role (only if a
    # custom access-token hook is added later). user_metadata is NEVER used.
    API_ROLE_SOURCE: Literal["table", "app_metadata"] = "table"
    API_RATE_LIMIT_PER_MINUTE: int = Field(default=60, ge=1, le=10_000)
    API_RATE_LIMIT_BURST: int = Field(default=20, ge=1, le=10_000)
    API_MAX_BODY_BYTES: int = Field(default=64 * 1024, ge=1024, le=1024 * 1024)
    API_TRUST_FORWARDED_FOR: bool = False
    API_DOCS_ENABLED: bool = False
    API_LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    API_UPSTREAM_TIMEOUT_SECONDS: float = Field(default=10.0, gt=0, le=60)

    # --- Manifest verification (public material only; never a private key) ----
    # Comma-separated base64 Ed25519 public keys (32 bytes each).
    MANIFEST_PUBLIC_KEY_B64: Annotated[list[str], NoDecode] = Field(default_factory=list)
    MANIFEST_REVOKED_KEY_IDS: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator(
        "API_ALLOWED_ORIGINS", "MANIFEST_PUBLIC_KEY_B64", "MANIFEST_REVOKED_KEY_IDS", mode="before"
    )
    @classmethod
    def _csv(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator("SUPABASE_URL")
    @classmethod
    def _strip_slash(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if value and not value.startswith(("https://", "http://localhost", "http://127.0.0.1")):
            raise ValueError("SUPABASE_URL must be https (http only for localhost)")
        return value

    @property
    def jwt_issuer(self) -> str:
        return f"{self.SUPABASE_URL}/auth/v1"

    @property
    def jwks_url(self) -> str:
        return f"{self.SUPABASE_URL}/auth/v1/.well-known/jwks.json"

    @property
    def production(self) -> bool:
        return self.API_ENV == "production"

    def missing_for_readiness(self) -> list[str]:
        """Names (never values) of settings needed to serve /v1 traffic."""
        missing: list[str] = []
        if not self.SUPABASE_URL:
            missing.append("SUPABASE_URL")
        if not self.SUPABASE_SERVICE_ROLE_KEY.get_secret_value():
            missing.append("SUPABASE_SERVICE_ROLE_KEY")
        if not (self.SUPABASE_JWT_SECRET.get_secret_value() or self.API_JWKS_ENABLED):
            missing.append("SUPABASE_JWT_SECRET")
        return missing


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
