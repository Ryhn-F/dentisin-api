"""CLI: sign_manifest.py {keygen,sign}. Prints JSON on stdout, warnings on stderr.

Never prints private key material.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from .canonical import JsonValue, parse_strict
from .errors import ErrorCode, ExitCode, ManifestError
from .keys import (
    ENV_PASSPHRASE,
    ENV_SIGNING_KEY_PATH,
    TEST_KEY_IDS,
    env_value,
    keygen,
    load_private_key,
)
from .schema import APP_VERSION_RE, MAX_MANIFEST_BYTES, format_timestamp, parse_timestamp
from .signing import (
    DEFAULT_RECOMMENDER_FILE,
    DEFAULT_RULES_FILE,
    BuildInputs,
    build_manifest,
    resolve_input,
    sign_manifest,
)
from .verify import check_local

ENV_BASE_URL: Final = "MANIFEST_BASE_URL"
SUBCOMMANDS: Final = ("keygen", "sign")


def _warn(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr)


def _emit(doc: dict[str, JsonValue]) -> None:
    print(json.dumps(doc, indent=2, sort_keys=True))


def _fail(exc: ManifestError) -> int:
    _emit({"ok": False, "error": {"code": str(exc.code), "message": exc.message}})
    return int(exc.exit_code)


def _read_passphrase(prompt: bool, confirm: bool) -> bytes | None:
    if not prompt:
        env = os.environ.get(ENV_PASSPHRASE)
        return env.encode("utf-8") if env else None
    first = getpass.getpass("Passphrase for the private key: ")
    if confirm and getpass.getpass("Repeat passphrase: ") != first:
        raise ManifestError(ErrorCode.E_USAGE, "passphrases do not match")
    if not first:
        raise ManifestError(ErrorCode.E_USAGE, "empty passphrase")
    return first.encode("utf-8")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sign_manifest.py",
        description="Build and sign the DentisAI model/rules manifest (Ed25519, SPEC.md).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    k = sub.add_parser("keygen", help="generate an Ed25519 key OUTSIDE the repository")
    k.add_argument(
        "--out",
        required=True,
        type=Path,
        help="private key path (outside the repo; not overwritten)",
    )
    k.add_argument(
        "--prompt-passphrase",
        action="store_true",
        help=f"encrypt the PEM with a prompted passphrase (else ${ENV_PASSPHRASE} if set)",
    )

    s = sub.add_parser("sign", help="sign an existing manifest or build one from artifacts")
    s.add_argument(
        "--key", type=Path, default=None, help=f"private key file (default ${ENV_SIGNING_KEY_PATH})"
    )
    s.add_argument("--prompt-passphrase", action="store_true", help="prompt for the key passphrase")
    s.add_argument("--manifest", type=Path, help="existing manifest JSON to (re)sign")
    s.add_argument(
        "--model-dir",
        type=Path,
        help="ml/exports/<semver> with model.tflite, labels.json, SHA256 (relative paths: "
        "CWD first, then backend/)",
    )
    s.add_argument(
        "--rules", type=Path, help="triage rules JSON (default backend/triage/dist/rules.json)"
    )
    s.add_argument(
        "--recommender",
        type=Path,
        help="recommender JSON (default backend/recommender/dist/recommender.json)",
    )
    s.add_argument(
        "--base-url", default=None, help=f"public bucket base URL (default ${ENV_BASE_URL})"
    )
    s.add_argument(
        "--sequence", type=int, help="monotonic sequence (must exceed the last published)"
    )
    s.add_argument("--min-app-version", help="MAJOR.MINOR.PATCH")
    s.add_argument("--rules-version")
    s.add_argument("--recommender-version")
    s.add_argument("--model-license")
    s.add_argument("--created-at", help="YYYY-MM-DDTHH:MM:SSZ (default: now)")
    exp = s.add_mutually_exclusive_group()
    exp.add_argument("--expires-at", help="YYYY-MM-DDTHH:MM:SSZ")
    exp.add_argument("--expires-in-days", type=int)
    s.add_argument(
        "--allow-placeholder",
        action="store_true",
        help='allow PLACEHOLDER/unverified content; stamps channel "internal-test"',
    )
    s.add_argument("--out-dir", type=Path, default=Path("manifest_out"))
    s.add_argument(
        "--force", action="store_true", help="overwrite manifest.json/manifest.sig in --out-dir"
    )
    return p


def _cmd_keygen(args: argparse.Namespace) -> int:
    passphrase = _read_passphrase(args.prompt_passphrase, confirm=True)
    key_id, pub = keygen(args.out, passphrase)
    if passphrase is None:
        _warn("private key written UNENCRYPTED; store it offline and keep an encrypted backup")
    _emit(
        {
            "ok": True,
            "key_id": key_id,
            "public_key_b64": pub,
            "private_key_path": str(args.out),
            "encrypted": passphrase is not None,
        }
    )
    return 0


def _build_doc(args: argparse.Namespace) -> tuple[dict[str, JsonValue], list[tuple[Path, str]]]:
    if args.manifest is not None:
        if any(getattr(args, a) is not None for a in ("model_dir", "rules", "recommender")):
            raise ManifestError(
                ErrorCode.E_USAGE, "use either --manifest or the build options, not both"
            )
        try:
            text = args.manifest.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ManifestError(ErrorCode.E_IO, f"cannot read {args.manifest}: {exc}") from None
        if len(text) > MAX_MANIFEST_BYTES * 4:
            raise ManifestError(ErrorCode.E_TOO_LARGE, "manifest input too large")
        doc = parse_strict(text)
        if not isinstance(doc, dict):
            raise ManifestError(ErrorCode.E_SCHEMA, "manifest must be a JSON object")
        return doc, []

    missing = [
        f"--{n.replace('_', '-')}"
        for n in ("model_dir", "sequence", "min_app_version")
        if getattr(args, n) is None
    ]
    base_url = args.base_url or env_value(ENV_BASE_URL)
    if not base_url:
        missing.append("--base-url")
        base_url = ""
    if missing:
        raise ManifestError(
            ErrorCode.E_USAGE, f"build mode needs {', '.join(missing)} (or use --manifest)"
        )
    if not APP_VERSION_RE.fullmatch(args.min_app_version):
        raise ManifestError(ErrorCode.E_USAGE, "--min-app-version must be MAJOR.MINOR.PATCH")
    if args.sequence < 1:
        raise ManifestError(ErrorCode.E_USAGE, "--sequence must be >= 1")
    try:
        created = (
            parse_timestamp(args.created_at)
            if args.created_at
            else datetime.now(UTC).replace(microsecond=0)
        )
        expires = parse_timestamp(args.expires_at) if args.expires_at else None
    except ManifestError as exc:
        raise ManifestError(ErrorCode.E_USAGE, exc.message) from None
    if args.expires_in_days is not None:
        expires = created + timedelta(days=args.expires_in_days)
    built = build_manifest(
        BuildInputs(
            model_dir=resolve_input(args.model_dir),
            rules_file=resolve_input(args.rules or DEFAULT_RULES_FILE),
            recommender_file=resolve_input(args.recommender or DEFAULT_RECOMMENDER_FILE),
            base_url=base_url,
            sequence=args.sequence,
            min_app_version=args.min_app_version,
            created_at=created,
            expires_at=expires,
            rules_version=args.rules_version,
            recommender_version=args.recommender_version,
            model_license=args.model_license,
        )
    )
    for w in built.warnings:
        _warn(w)
    if built.findings:
        for f in built.findings:
            _warn(f"placeholder/unverified: {f}")
        if not args.allow_placeholder:
            raise ManifestError(
                ErrorCode.E_REFUSED,
                f"{len(built.findings)} placeholder/unverified marker(s) found; fix them or pass "
                "--allow-placeholder (signs as channel internal-test, rejected in production)",
            )
    return built.manifest, built.files


def _cmd_sign(args: argparse.Namespace) -> int:
    env_key = env_value(ENV_SIGNING_KEY_PATH)
    key_path = args.key or (Path(env_key) if env_key else None)
    if key_path is None:
        raise ManifestError(
            ErrorCode.E_USAGE,
            f"no key: pass --key or set {ENV_SIGNING_KEY_PATH} (environment or backend/.env)",
        )
    out_dir: Path = args.out_dir
    targets = (out_dir / "manifest.json", out_dir / "manifest.sig")
    if not args.force and any(t.exists() for t in targets):
        raise ManifestError(
            ErrorCode.E_REFUSED, f"{out_dir} already has manifest.json/.sig; use --force"
        )

    doc, files = _build_doc(args)
    passphrase = (
        _read_passphrase(args.prompt_passphrase, confirm=False) if args.prompt_passphrase else None
    )
    loaded = load_private_key(key_path, passphrase)
    for w in loaded.warnings:
        _warn(w)
    if loaded.key_id in TEST_KEY_IDS and not args.allow_placeholder:
        raise ManifestError(
            ErrorCode.E_REFUSED, "refusing to sign a production manifest with the public TEST key"
        )
    signed = sign_manifest(loaded.private_key, doc, allow_placeholder=args.allow_placeholder)

    out_dir.mkdir(parents=True, exist_ok=True)
    for src, rel in files:
        dest = out_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    if files:
        bad = [r for r in check_local(signed.manifest, out_dir) if not r.ok]
        if bad:  # pragma: no cover - defensive: files changed while copying
            raise ManifestError(
                ErrorCode.E_ARTIFACT_HASH, f"staged artifact {bad[0].name} changed during copy"
            )
    targets[0].write_bytes(signed.manifest_bytes)
    targets[1].write_bytes(signed.sig_bytes)
    if signed.manifest.channel != "production":
        _warn(
            f'signed with channel "{signed.manifest.channel}": production verifiers will reject it'
        )
    _emit(
        {
            "ok": True,
            "manifest_path": str(targets[0]),
            "sig_path": str(targets[1]),
            "staged_artifacts": [rel for _, rel in files],
            "key_id": signed.key_id,
            "public_key_b64": signed.public_key_b64,
            "sequence": signed.manifest.sequence,
            "channel": signed.manifest.channel,
            "created_at": format_timestamp(signed.manifest.created_at),
            "manifest_sha256": hashlib.sha256(signed.manifest_bytes).hexdigest(),
        }
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if not args_list or args_list[0] not in (*SUBCOMMANDS, "-h", "--help"):
        args_list.insert(
            0, "sign"
        )  # backwards-compatible: sign_manifest.py --key ... --manifest ...
    args = _parser().parse_args(args_list)
    try:
        if args.command == "keygen":
            return _cmd_keygen(args)
        return _cmd_sign(args)
    except ManifestError as exc:
        return _fail(exc)
    except OSError as exc:
        return _fail(ManifestError(ErrorCode.E_IO, f"{exc.filename or ''}: {exc.strerror}"))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

__all__ = ["ExitCode", "main"]
