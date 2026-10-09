"""CLI: verify_manifest.py. Always prints one JSON report on stdout.

Exit codes: 0 ok, 1 I/O, 2 usage, 3 signature/key, 4 format/schema,
5 policy (expired, rollback, downgrade, channel, app too old), 6 artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from .errors import ErrorCode, ExitCode, ManifestError
from .keys import ENV_PUBLIC_KEY_B64, env_value
from .schema import APP_VERSION_RE, KEY_ID_RE, MAX_MANIFEST_BYTES, MAX_SIG_BYTES, parse_timestamp
from .verify import (
    DEFAULT_TIMEOUT_S,
    Opener,
    Policy,
    Report,
    TrustStore,
    check_local,
    check_remote,
    fetch_capped,
    first_failure,
    read_binary,
    verify_manifest,
)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="verify_manifest.py",
        description="Verify a DentisAI manifest exactly as the app does (SPEC.md section 5).",
    )
    p.add_argument("--manifest", required=True, help="manifest.json path or https URL")
    p.add_argument(
        "--sig", default=None, help="manifest.sig path or URL (default: sibling manifest.sig)"
    )
    p.add_argument(
        "--pubkey",
        action="append",
        default=None,
        help=f"trusted base64 raw public key (repeatable; default ${ENV_PUBLIC_KEY_B64}, "
        "comma-separated)",
    )
    p.add_argument("--revoked", action="append", default=[], help="revoked key_id (repeatable)")
    p.add_argument(
        "--production",
        action="store_true",
        help='production policy: reject channel "internal-test", PLACEHOLDER and the TEST key',
    )
    p.add_argument("--now", default=None, help="override current time (YYYY-MM-DDTHH:MM:SSZ)")
    p.add_argument("--last-sequence", type=int, default=None)
    p.add_argument("--last-manifest-sha256", default=None)
    p.add_argument("--last-schema-version", type=int, default=None)
    p.add_argument("--app-version", default=None, help="app version MAJOR.MINOR.PATCH")
    art = p.add_mutually_exclusive_group()
    art.add_argument("--artifacts-dir", type=Path, default=None, help="local mirror of the bucket")
    art.add_argument(
        "--fetch", action="store_true", help="download artifacts over https and check them"
    )
    p.add_argument("--base-url", default=None, help="URL prefix mapped to --artifacts-dir")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    return p


def _default_sig(manifest: str) -> str:
    if manifest.startswith("https://"):
        return manifest.rsplit("/", 1)[0] + "/manifest.sig"
    return str(Path(manifest).with_name("manifest.sig"))


def _read(src: str, cap: int, timeout: float, opener: Opener | None) -> bytes:
    if src.startswith(("https://", "http://")):
        return fetch_capped(src, cap, timeout, opener)
    try:
        with open(src, "rb") as fh:
            return read_binary(fh, cap)
    except OSError as exc:
        raise ManifestError(ErrorCode.E_IO, f"cannot read {src}: {exc.strerror}") from None


def _policy(args: argparse.Namespace) -> Policy:
    try:
        now = parse_timestamp(args.now, "--now") if args.now else datetime.now(UTC)
    except ManifestError as exc:
        raise ManifestError(ErrorCode.E_USAGE, exc.message) from None
    if args.app_version is not None and not APP_VERSION_RE.fullmatch(args.app_version):
        raise ManifestError(ErrorCode.E_USAGE, "--app-version must be MAJOR.MINOR.PATCH")
    return Policy(
        now=now,
        production=args.production,
        last_sequence=args.last_sequence,
        last_manifest_sha256=args.last_manifest_sha256,
        last_schema_version=args.last_schema_version,
        app_version=args.app_version,
    )


def _trust(args: argparse.Namespace) -> TrustStore:
    keys: list[str] = list(args.pubkey or [])
    if not keys:
        keys = [k for k in (env_value(ENV_PUBLIC_KEY_B64) or "").split(",") if k.strip()]
    if not keys:
        raise ManifestError(
            ErrorCode.E_USAGE, f"no trusted public key: pass --pubkey or set {ENV_PUBLIC_KEY_B64}"
        )
    for kid in args.revoked:
        if not KEY_ID_RE.fullmatch(kid):
            raise ManifestError(ErrorCode.E_USAGE, f"--revoked {kid!r} is not a 16-hex key_id")
    try:
        return TrustStore.from_b64(keys, args.revoked)
    except ValueError as exc:
        raise ManifestError(ErrorCode.E_USAGE, str(exc)) from None


def run(args: argparse.Namespace, opener: Opener | None = None) -> Report:
    report = Report(ok=False, exit_code=int(ExitCode.IO))
    try:
        policy = _policy(args)
        trust = _trust(args)
        raw_manifest = _read(args.manifest, MAX_MANIFEST_BYTES, args.timeout, opener)
        raw_sig = _read(
            args.sig or _default_sig(args.manifest), MAX_SIG_BYTES, args.timeout, opener
        )
        report.manifest_sha256 = hashlib.sha256(raw_manifest).hexdigest()
        manifest = verify_manifest(raw_manifest, raw_sig, trust, policy)
        report.manifest = manifest.summary()
        results = []
        if args.artifacts_dir is not None:
            results = check_local(manifest, args.artifacts_dir, args.base_url)
        elif args.fetch:
            results = check_remote(manifest, args.timeout, opener)
        report.artifacts = [r.to_json() for r in results]
        bad = first_failure(results)
        if bad is not None:
            assert bad.code is not None
            raise ManifestError(ErrorCode(bad.code), f"artifact {bad.name}: {bad.message}")
    except ManifestError as exc:
        report.error = {"code": str(exc.code), "message": exc.message}
        report.exit_code = int(exc.exit_code)
        return report
    report.ok, report.exit_code = True, 0
    return report


def main(argv: list[str] | None = None, opener: Opener | None = None) -> int:
    args = _parser().parse_args(argv)
    report = run(args, opener)
    print(json.dumps(report.to_json(), indent=2, sort_keys=True))
    return report.exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
