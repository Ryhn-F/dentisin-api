"""Model/rules version registration: verify the signed manifest, then record it.

The private signing key never touches the server (it stays offline on the
team PC, backend/CLAUDE.md §7). Registration is idempotent per manifest
sha256; rollback/equivocation is rejected against the highest registered
sequence (the same rule the app applies, step 11 of the verifier).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from ..auth import Principal
from ..errors import ApiError, conflict, unavailable
from ..repositories import tables as t
from ..repositories.base import DuplicateError, MlRepository, ModelRecord, NewModel
from .manifest_verify import (
    E_ROLLBACK,
    E_TOO_LARGE,
    JsonValue,
    ManifestError,
    Policy,
    TrustStore,
    VerifiedManifest,
    verify_manifest,
)

log = logging.getLogger("dentisai.api.models")

_POLICY_CODES = frozenset(
    {"E_SCHEMA_DOWNGRADE", "E_EXPIRED", "E_CHANNEL", "E_PLACEHOLDER", "E_APP_TOO_OLD"}
)


def _status_for(code: str) -> int:
    if code == E_ROLLBACK:
        return 409
    if code == E_TOO_LARGE:
        return 413
    return 422


def _s(component: dict[str, JsonValue], key: str) -> str:
    value = component.get(key)
    return value if isinstance(value, str) else ""


def _new_model(
    manifest: VerifiedManifest, canonical: str, sig: str, principal: Principal
) -> NewModel:
    model = manifest.component("model")
    return NewModel(
        manifest_sha256=manifest.manifest_sha256,
        sequence=manifest.sequence,
        schema_version=manifest.schema_version,
        channel=manifest.channel,
        signer_key_id=manifest.signer_key_id,
        min_app_version=manifest.min_app_version,
        model_version=_s(model, "version"),
        model_sha256=_s(model, "sha256"),
        model_license=_s(model, "license"),
        rules_version=_s(manifest.component("rules"), "version"),
        recommender_version=_s(manifest.component("recommender"), "version"),
        manifest_created_at=manifest.created_at,
        expires_at=manifest.expires_at,
        manifest_canonical=canonical,
        manifest_sig=sig,
        registered_by=principal.user_id,
    )


async def register(
    repo: MlRepository,
    trust: TrustStore | None,
    production: bool,
    principal: Principal,
    manifest_json: str,
    manifest_sig: str,
) -> tuple[ModelRecord, bool]:
    if trust is None or not trust.keys:
        raise unavailable(
            "manifest_trust_not_configured",
            "No trusted manifest public key is configured on the server.",
        )
    try:
        raw_manifest = manifest_json.encode("ascii")
        raw_sig = manifest_sig.encode("ascii")
    except UnicodeEncodeError:
        raise ApiError(
            422, "E_NOT_CANONICAL", "Manifest must be printable ASCII (DCJ-1)."
        ) from None

    latest = await repo.latest_model()
    policy = Policy(
        now=datetime.now(UTC),
        production=production,
        last_sequence=latest.sequence if latest else None,
        last_manifest_sha256=latest.manifest_sha256 if latest else None,
        last_schema_version=latest.schema_version if latest else None,
    )
    try:
        manifest = verify_manifest(raw_manifest, raw_sig, trust, policy)
    except ManifestError as exc:
        log.info("manifest_rejected", extra={"code": exc.code})
        # exc.message is a fixed, data-free string from the verifier.
        raise ApiError(_status_for(exc.code), exc.code, exc.message) from None

    existing = await repo.get_model_by_sha(manifest.manifest_sha256)
    if existing is not None:
        return existing, False
    try:
        record = await repo.insert_model(
            _new_model(manifest, manifest_json, manifest_sig, principal)
        )
    except DuplicateError:
        # Concurrent registration of the same sequence with other bytes.
        raise conflict(E_ROLLBACK, "A manifest with this sequence is already registered.") from None
    await repo.audit(
        principal.user_id,
        "ml_model_registered",
        t.MODEL_REGISTRY,
        record.id,
        {"sequence": record.sequence, "channel": record.channel},
    )
    return record, True
