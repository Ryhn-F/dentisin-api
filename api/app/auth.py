"""Supabase JWT verification. The API has NO auth of its own (ADR 005): it only
verifies access tokens issued by Supabase Auth.

Supported signing schemes (both configurable, both may be active during a key
rotation):
- Asymmetric JWT signing keys (current Supabase default for new projects:
  ES256, also RS256/EdDSA) verified against the project's JWKS at
  ``<SUPABASE_URL>/auth/v1/.well-known/jwks.json`` (``API_JWKS_ENABLED``).
- Legacy HS256 shared secret (``SUPABASE_JWT_SECRET``).

Checks: allowed alg only (``none`` and anything unexpected are rejected before
any key lookup), signature, ``exp`` (required), ``iat``/``nbf`` if present,
``aud == "authenticated"``, ``iss == <SUPABASE_URL>/auth/v1``, ``sub`` is a
UUID, and the Postgres role claim ``role == "authenticated"`` (anon and
service_role tokens are not user tokens).

App roles (admin, ...) are NEVER taken from ``user_metadata`` (user-editable).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final

import httpx
import jwt
from jwt import PyJWK
from jwt.exceptions import InvalidKeyError, InvalidTokenError, PyJWKError

from .errors import unauthorized
from .settings import Settings

log = logging.getLogger("dentisai.api.auth")

ASYMMETRIC_ALGS: Final = frozenset({"ES256", "RS256", "EdDSA"})
SYMMETRIC_ALGS: Final = frozenset({"HS256"})
AUDIENCE: Final = "authenticated"
MAX_TOKEN_LENGTH: Final = 8192
JWKS_MIN_REFRESH_INTERVAL_S: Final = 30.0


@dataclass(frozen=True)
class Principal:
    user_id: str
    roles: frozenset[str]
    is_anonymous: bool = False

    def has_any(self, allowed: frozenset[str]) -> bool:
        return bool(self.roles & allowed)


@dataclass(frozen=True)
class VerifiedToken:
    sub: str
    claims: dict[str, Any]


JwksFetcher = Callable[[], Awaitable[dict[str, Any]]]


class JwksCache:
    """Caches the JWKS; refetches on TTL expiry or on an unknown ``kid``
    (rate-limited so random kids cannot be used to hammer Supabase)."""

    def __init__(
        self,
        fetcher: JwksFetcher,
        ttl_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetcher = fetcher
        self._ttl = ttl_s
        self._clock = clock
        self._keys: dict[str, PyJWK] = {}
        self._fetched_at: float | None = None
        self._lock = asyncio.Lock()

    async def _refresh(self) -> None:
        doc = await self._fetcher()
        keys: dict[str, PyJWK] = {}
        raw_keys = doc.get("keys")
        if isinstance(raw_keys, list):
            for raw in raw_keys:
                if not isinstance(raw, dict):
                    continue
                kid = raw.get("kid")
                if not isinstance(kid, str) or raw.get("use", "sig") != "sig":
                    continue
                try:
                    keys[kid] = PyJWK(raw)
                except (PyJWKError, InvalidKeyError):
                    continue  # unsupported key type: skip, never trust
        self._keys = keys
        self._fetched_at = self._clock()

    async def get(self, kid: str) -> PyJWK | None:
        async with self._lock:
            now = self._clock()
            stale = self._fetched_at is None or now - self._fetched_at > self._ttl
            unknown_and_may_retry = kid not in self._keys and (
                self._fetched_at is None or now - self._fetched_at > JWKS_MIN_REFRESH_INTERVAL_S
            )
            if stale or unknown_and_may_retry:
                try:
                    await self._refresh()
                except (httpx.HTTPError, ValueError):
                    log.warning("jwks_fetch_failed", extra={"event": "jwks_fetch_failed"})
            return self._keys.get(kid)


def http_jwks_fetcher(client: httpx.AsyncClient, settings: Settings) -> JwksFetcher:
    async def fetch() -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        anon = settings.SUPABASE_ANON_KEY.get_secret_value()
        if anon:
            headers["apikey"] = anon
        resp = await client.get(
            settings.jwks_url, headers=headers, timeout=settings.API_UPSTREAM_TIMEOUT_SECONDS
        )
        resp.raise_for_status()
        doc = resp.json()
        if not isinstance(doc, dict):
            raise ValueError("jwks is not an object")
        return doc

    return fetch


class TokenVerifier:
    def __init__(self, settings: Settings, jwks: JwksCache | None) -> None:
        self._settings = settings
        self._jwks = jwks if settings.API_JWKS_ENABLED else None
        self._secret = settings.SUPABASE_JWT_SECRET.get_secret_value()

    async def verify(self, token: str) -> VerifiedToken:
        if not token or len(token) > MAX_TOKEN_LENGTH or not self._settings.SUPABASE_URL:
            raise unauthorized("invalid_token", "Invalid or expired token.")
        try:
            header = jwt.get_unverified_header(token)
        except InvalidTokenError:
            raise unauthorized("invalid_token", "Invalid or expired token.") from None
        alg = header.get("alg")
        key: Any
        if alg in SYMMETRIC_ALGS and self._secret:
            key = self._secret
        elif alg in ASYMMETRIC_ALGS and self._jwks is not None:
            kid = header.get("kid")
            if not isinstance(kid, str) or not kid or len(kid) > 128:
                raise unauthorized("invalid_token", "Invalid or expired token.")
            jwk = await self._jwks.get(kid)
            if jwk is None or jwk.algorithm_name != alg:
                raise unauthorized("invalid_token", "Invalid or expired token.")
            key = jwk.key
        else:
            # Includes alg "none", HS* when no secret is configured (prevents
            # key-confusion with a public key), and any unexpected algorithm.
            raise unauthorized("invalid_token", "Invalid or expired token.")
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key=key,
                algorithms=[alg],
                audience=AUDIENCE,
                issuer=self._settings.jwt_issuer,
                leeway=self._settings.API_JWT_LEEWAY_SECONDS,
                options={"require": ["exp", "sub", "aud", "iss"], "verify_signature": True},
            )
        except jwt.ExpiredSignatureError:
            raise unauthorized("token_expired", "Invalid or expired token.") from None
        except InvalidTokenError:
            raise unauthorized("invalid_token", "Invalid or expired token.") from None
        sub = claims.get("sub")
        if not isinstance(sub, str) or not _is_uuid(sub) or claims.get("role") != "authenticated":
            raise unauthorized("invalid_token", "Invalid or expired token.")
        return VerifiedToken(sub=sub.lower(), claims=claims)


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return len(value) == 36


def roles_from_app_metadata(claims: dict[str, Any]) -> frozenset[str]:
    """Roles from the server-controlled ``app_metadata`` claim only.

    Accepts ``app_metadata.roles`` (list of str) and/or ``app_metadata.role``
    (str). ``user_metadata`` is deliberately never read: users can edit it.
    """
    meta = claims.get("app_metadata")
    if not isinstance(meta, dict):
        return frozenset()
    roles: set[str] = set()
    raw_list = meta.get("roles")
    if isinstance(raw_list, list):
        roles.update(r for r in raw_list if isinstance(r, str))
    raw_one = meta.get("role")
    if isinstance(raw_one, str):
        roles.add(raw_one)
    return frozenset(r for r in roles if r.isidentifier() and len(r) <= 40)


def parse_bearer(authorization: str | None) -> str:
    if not authorization:
        raise unauthorized()
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise unauthorized("invalid_token", "Invalid or expired token.")
    return token.strip()
