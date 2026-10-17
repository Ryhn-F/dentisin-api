"""FastAPI dependencies: application state, authentication, role checks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated, Final

import httpx
from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .auth import Principal, TokenVerifier, roles_from_app_metadata
from .errors import ApiError, forbidden, unauthorized
from .ratelimit import TokenBucketLimiter
from .repositories.base import MlRepository
from .services.detector import Detector
from .services.manifest_verify import TrustStore
from .services.quality import QualityGate, UnavailableQualityGate
from .services.screening_rules import ScreeningRules
from .settings import Settings

# Role model of backend/supabase (public.app_role, ADR 017/018): guardian (orang tua
# AND remaja/teen accounts), child (child accounts created by a guardian), admin.
# The retired values kader / facility_viewer (ADR 018) grant nothing here. Child
# accounts only record brushing and request screenings (ADR 017), so they are in
# none of the sets below. There is no `reviewer` role in the schema, so dentist
# reviewers are `admin` accounts (granted via public.admin_grant_role). If a
# `reviewer` role is added later, add it to REVIEWER_ROLES only.
ADMIN_ROLES: Final = frozenset({"admin"})
REVIEWER_ROLES: Final = frozenset({"admin"})
# /v1/screening/evaluate: the people who run a screening in the app (guardian/teen).
SCREENING_ROLES: Final = frozenset({"guardian", "admin"})
# Metadata (rules/schema): any app role that screens; anonymous never.
ANY_APP_ROLE: Final = frozenset({"guardian", "admin"})


@dataclass
class AppState:
    settings: Settings
    repo: MlRepository
    verifier: TokenVerifier
    user_limiter: TokenBucketLimiter
    detector: Detector
    trust_store: TrustStore | None
    http_client: httpx.AsyncClient | None = None
    # None = rule artefacts missing/invalid -> screening answers 503 rules_unavailable.
    screening: ScreeningRules | None = None
    quality_gate: QualityGate = field(default_factory=UnavailableQualityGate)


def get_state(request: Request) -> AppState:
    state: AppState = request.app.state.dentisai
    return state


StateDep = Annotated[AppState, Depends(get_state)]

_bearer = HTTPBearer(auto_error=False, description="Supabase Auth access token (JWT).")


async def get_principal(
    request: Request,
    state: StateDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(_bearer)],
) -> Principal:
    if credentials is None:
        # Distinguish "no header" from "malformed header" without echoing it.
        if request.headers.get("authorization"):
            raise unauthorized("invalid_token", "Invalid or expired token.")
        raise unauthorized()
    token = await state.verifier.verify(credentials.credentials)
    allowed, retry_after = state.user_limiter.acquire("sub:" + token.sub)
    if not allowed:
        raise ApiError(
            429, "rate_limited", "Too many requests.", headers={"Retry-After": str(retry_after)}
        )
    if state.settings.API_ROLE_SOURCE == "app_metadata":
        roles = roles_from_app_metadata(token.claims)
    else:
        roles = await state.repo.get_roles(token.sub)
    return Principal(
        user_id=token.sub,
        roles=roles,
        is_anonymous=token.claims.get("is_anonymous") is True,
    )


PrincipalDep = Annotated[Principal, Depends(get_principal)]


def require_roles(allowed: frozenset[str]) -> Callable[[Principal], Awaitable[Principal]]:
    async def _check(principal: PrincipalDep) -> Principal:
        if principal.is_anonymous or not principal.has_any(allowed):
            raise forbidden()
        return principal

    return _check


AdminDep = Annotated[Principal, Depends(require_roles(ADMIN_ROLES))]
ReviewerDep = Annotated[Principal, Depends(require_roles(REVIEWER_ROLES))]
ScreenerDep = Annotated[Principal, Depends(require_roles(SCREENING_ROLES))]
AnyRoleDep = Annotated[Principal, Depends(require_roles(ANY_APP_ROLE))]
