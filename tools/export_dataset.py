#!/usr/bin/env python3
"""Export consented dataset photos to the team PC (ADMIN TOOL, service_role, PC team only).

Purpose: pull photos from the private ``dataset_photos`` bucket for LOCAL annotation
(CVAT / Label Studio). It never uploads anything and never touches Kaggle/Colab.

What it does
  1. Requires ``--i-have-ethics-approval`` AND the ``dataset_contribution`` feature flag ON
     (queried live). Otherwise it refuses to run.
  2. Refuses unless DATASET_EXPORT_DIR exists and is OUTSIDE the backend/repository tree.
  3. Lists uploads whose owner has an ACTIVE, not-withdrawn consent of kind (c), and whose
     own upload row is not withdrawn.
  4. ``--dry-run`` (default) only prints counts. ``--execute`` downloads each photo to
     ``<uuid>.jpg`` (fresh random UUID, no link to any user id) and appends to
     ``manifest.csv`` with columns: uuid, sha256, bytes, consent_version, exported_at.
  5. Photos are stored byte-for-byte; nothing is stripped. A photo that still contains
     EXIF/XMP metadata (should have been removed on the phone) is SKIPPED unless
     ``--allow-metadata`` is given.

Store the export directory on an ENCRYPTED disk (BitLocker/VeraCrypt). This tool encrypts
nothing itself. Do not sync it to cloud drives. Withdrawn consent: re-export into a NEW
folder periodically and replace the old set; the manifest deliberately holds no user ids,
so a previously exported photo cannot be traced back to its owner for deletion.
Supabase egress is 5 GB/month on Free: a full re-export of ~2,000 photos is about 1 GB.

Schema ASSUMPTIONS (code against another engineer's migrations, isolated below)
  feature_flag(key, enabled)
  consent(guardian_id, kind 'a'|'b'|'c', text_version, granted_at?, withdrawn_at? | withdrawn?)
  image_upload(guardian_id, storage_path, withdrawn_at? | withdrawn?)   -- catalog of objects
  Storage bucket ``dataset_photos`` (private). ``granted_at`` is optional: without it, an
  owner with ANY withdrawn (c) row is excluded (fail closed).
If a name differs, fix it in the SCHEMA block only.

Environment: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, DATASET_EXPORT_DIR.
Exit codes: 0 ok, 1 some photos failed, 2 usage/config, 3 refused (flag off / unsafe dir /
no approval), 4 API error (cannot verify consent => nothing exported).
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import os
import sys
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from opscommon import (
    ConfigError,
    get_env,
    is_inside_any,
    load_env_file,
    protected_roots,
    repo_root,
    require_env,
)

# --------------------------------------------------------------------------------------
# SCHEMA block: every table/column/bucket name this tool assumes lives here.
# --------------------------------------------------------------------------------------
BUCKET = "dataset_photos"
FLAG_TABLE = "feature_flag"
FLAG_KEY_COL = "key"
FLAG_ENABLED_COL = "enabled"
FLAG_NAME = "dataset_contribution"
CONSENT_TABLE = "consent"
CONSENT_OWNER_COL = "guardian_id"
CONSENT_KIND_COL = "kind"
CONSENT_KIND_DATASET = "c"
CONSENT_VERSION_COL = "text_version"
CONSENT_GRANTED_AT_COL = "granted_at"  # optional
CONSENT_WITHDRAWN_AT_COL = "withdrawn_at"  # optional (timestamp, null = active)
CONSENT_WITHDRAWN_BOOL_COL = "withdrawn"  # optional (boolean)
UPLOAD_TABLE = "image_upload"
UPLOAD_OWNER_COL = "guardian_id"
UPLOAD_PATH_COL = "storage_path"  # object path inside BUCKET
UPLOAD_WITHDRAWN_AT_COL = "withdrawn_at"  # optional
UPLOAD_WITHDRAWN_BOOL_COL = "withdrawn"  # optional
# --------------------------------------------------------------------------------------

MANIFEST_NAME = "manifest.csv"
MANIFEST_COLUMNS = ("uuid", "sha256", "bytes", "consent_version", "exported_at")
PAGE_SIZE = 1000

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_USAGE = 2
EXIT_REFUSED = 3
EXIT_API = 4

STORAGE_ADVICE = (
    "Store the export on an ENCRYPTED disk (BitLocker/VeraCrypt), keep it off cloud sync, and "
    "never upload it to Kaggle/Colab or any third party (annotate locally in CVAT/Label Studio)."
)


class ExportError(Exception):
    """API or data problem; message never contains row contents."""


class Refused(Exception):
    """A safety precondition failed."""


@dataclass(frozen=True)
class Candidate:
    storage_path: str
    consent_version: str


@dataclass
class Plan:
    candidates: list[Candidate]
    owners_eligible: int
    uploads_total: int
    skipped_no_consent: int
    skipped_withdrawn: int
    skipped_bad_path: int


def is_withdrawn(row: Mapping[str, Any], at_col: str, bool_col: str) -> bool:
    return bool(row.get(at_col)) or row.get(bool_col) is True


def fetch_all(client: httpx.Client, table: str, params: Mapping[str, str]) -> list[dict[str, Any]]:
    """Fetch all rows of a PostgREST table (paged). Never logs row contents."""
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        headers = {"Range-Unit": "items", "Range": f"{start}-{start + PAGE_SIZE - 1}"}
        response = client.get(f"/rest/v1/{table}", params=dict(params), headers=headers)
        if response.status_code not in (200, 206):
            raise ExportError(
                f"PostgREST query on '{table}' failed with HTTP {response.status_code} "
                "(check the SCHEMA block in export_dataset.py and your service key)."
            )
        page = response.json()
        if not isinstance(page, list):
            raise ExportError(f"Unexpected response shape from '{table}'.")
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            return rows
        start += PAGE_SIZE


def flag_enabled(client: httpx.Client) -> bool:
    rows = fetch_all(client, FLAG_TABLE, {"select": "*", FLAG_KEY_COL: f"eq.{FLAG_NAME}"})
    return any(row.get(FLAG_ENABLED_COL) is True for row in rows)


def active_consents(rows: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """Map owner -> consent text version for owners whose (c) consent is active."""
    by_owner: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        if row.get(CONSENT_KIND_COL) != CONSENT_KIND_DATASET:
            continue
        owner = row.get(CONSENT_OWNER_COL)
        if isinstance(owner, str) and owner:
            by_owner.setdefault(owner, []).append(row)
    result: dict[str, str] = {}
    for owner, owner_rows in by_owner.items():
        withdrawn = [
            is_withdrawn(r, CONSENT_WITHDRAWN_AT_COL, CONSENT_WITHDRAWN_BOOL_COL)
            for r in owner_rows
        ]
        ordered = all(isinstance(r.get(CONSENT_GRANTED_AT_COL), str) for r in owner_rows)
        if ordered:
            newest = max(owner_rows, key=lambda r: str(r[CONSENT_GRANTED_AT_COL]))
            if is_withdrawn(newest, CONSENT_WITHDRAWN_AT_COL, CONSENT_WITHDRAWN_BOOL_COL):
                continue
        elif any(withdrawn):  # no ordering info: fail closed
            continue
        else:
            newest = owner_rows[0]
        result[owner] = str(newest.get(CONSENT_VERSION_COL) or "unknown")
    return result


def safe_object_path(path: object) -> str | None:
    if not isinstance(path, str) or not path or path.startswith(("/", "\\")):
        return None
    parts = path.replace("\\", "/").split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    return path


def build_plan(client: httpx.Client) -> Plan:
    consent_rows = fetch_all(
        client, CONSENT_TABLE, {"select": "*", CONSENT_KIND_COL: f"eq.{CONSENT_KIND_DATASET}"}
    )
    consents = active_consents(consent_rows)
    uploads = fetch_all(client, UPLOAD_TABLE, {"select": "*"})
    candidates: list[Candidate] = []
    no_consent = withdrawn = bad_path = 0
    owners: set[str] = set()
    for row in uploads:
        path = safe_object_path(row.get(UPLOAD_PATH_COL))
        if path is None:
            bad_path += 1
            continue
        if is_withdrawn(row, UPLOAD_WITHDRAWN_AT_COL, UPLOAD_WITHDRAWN_BOOL_COL):
            withdrawn += 1
            continue
        owner = row.get(UPLOAD_OWNER_COL)
        version = consents.get(owner) if isinstance(owner, str) else None
        if version is None:
            no_consent += 1
            continue
        owners.add(str(owner))
        candidates.append(Candidate(path, version))
    return Plan(candidates, len(owners), len(uploads), no_consent, withdrawn, bad_path)


def has_embedded_metadata(data: bytes) -> bool:
    """True if a JPEG carries an EXIF or XMP segment (location/camera metadata)."""
    if data[:2] != b"\xff\xd8":
        return False
    i = 2
    n = len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            return False
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if marker in (0xDA, 0xD9):  # start of scan / EOI: no more headers
            return False
        length = int.from_bytes(data[i + 2 : i + 4], "big")
        payload = data[i + 4 : i + 2 + length]
        if marker == 0xE1 and (
            payload.startswith(b"Exif\x00\x00") or payload.startswith(b"http://ns.adobe.com/xap/")
        ):
            return True
        i += 2 + length
    return False


def validate_export_dir(raw: str | None, roots: Sequence[Path]) -> Path:
    if not raw:
        raise Refused("DATASET_EXPORT_DIR is not set.")
    path = Path(raw).expanduser()
    if not path.is_dir():
        raise Refused("DATASET_EXPORT_DIR does not exist or is not a directory (create it first).")
    if is_inside_any(path, roots):
        raise Refused(
            "DATASET_EXPORT_DIR is inside the backend/repository tree; choose a folder outside."
        )
    return path.resolve()


def validate_supabase_url(url: str) -> str:
    parsed = urlparse(url)
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
        raise ConfigError("SUPABASE_URL must be an https URL.")
    return url.rstrip("/")


def _load_known_hashes(manifest: Path) -> set[str]:
    if not manifest.is_file():
        return set()
    with manifest.open(newline="", encoding="utf-8") as fh:
        return {row["sha256"] for row in csv.DictReader(fh) if row.get("sha256")}


def export_photos(
    client: httpx.Client,
    plan: Plan,
    out_dir: Path,
    *,
    allow_metadata: bool,
    now: datetime | None = None,
) -> dict[str, int]:
    manifest = out_dir / MANIFEST_NAME
    known = _load_known_hashes(manifest)
    stats = {"exported": 0, "duplicate": 0, "missing": 0, "metadata": 0, "invalid": 0, "error": 0}
    new_manifest = not manifest.exists()
    stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with manifest.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if new_manifest:
            writer.writerow(MANIFEST_COLUMNS)
        for cand in plan.candidates:
            url = f"/storage/v1/object/authenticated/{BUCKET}/{quote(cand.storage_path)}"
            try:
                response = client.get(url)
            except httpx.HTTPError:
                stats["error"] += 1
                continue
            if response.status_code == 404:
                stats["missing"] += 1
                continue
            if response.status_code != 200:
                stats["error"] += 1
                continue
            data = response.content
            if data[:3] != b"\xff\xd8\xff":
                stats["invalid"] += 1
                continue
            if has_embedded_metadata(data) and not allow_metadata:
                stats["metadata"] += 1
                continue
            digest = hashlib.sha256(data).hexdigest()
            if digest in known:
                stats["duplicate"] += 1
                continue
            name = str(uuid.uuid4())
            target = out_dir / f"{name}.jpg"
            with target.open("xb") as photo:
                photo.write(data)
            with contextlib.suppress(OSError):
                target.chmod(0o600)
            writer.writerow([name, digest, len(data), cand.consent_version, stamp])
            fh.flush()
            known.add(digest)
            stats["exported"] += 1
    return stats


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Export consented dataset photos to the team PC (admin tool).",
        epilog=STORAGE_ADVICE,
    )
    p.add_argument(
        "--i-have-ethics-approval",
        action="store_true",
        help="Required. Confirms KEPK/ethics approval covers this export.",
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Default: print counts only.")
    mode.add_argument("--execute", action="store_true", help="Actually download photos.")
    p.add_argument(
        "--allow-metadata",
        action="store_true",
        help="Also export JPEGs that still contain EXIF/XMP (default: skip them).",
    )
    return p


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
    client: httpx.Client | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    if environ is None:
        live = os.environ
        load_env_file(repo_root() / ".env", live)
        environ = live

    def log(msg: str) -> None:
        print(f"export_dataset: {msg}", file=sys.stderr)

    if not args.i_have_ethics_approval:
        log("refused: pass --i-have-ethics-approval once KEPK approval covers dataset export.")
        return EXIT_REFUSED
    try:
        env = require_env(
            ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "DATASET_EXPORT_DIR"), environ
        )
        base_url = validate_supabase_url(env["SUPABASE_URL"])
        out_dir = validate_export_dir(get_env("DATASET_EXPORT_DIR", environ), protected_roots())
    except ConfigError as exc:
        log(str(exc))
        return EXIT_USAGE
    except Refused as exc:
        log(f"refused: {exc}")
        return EXIT_REFUSED

    key = env["SUPABASE_SERVICE_ROLE_KEY"]
    owns_client = client is None
    http = client or httpx.Client(timeout=60.0)
    http.base_url = httpx.URL(base_url)
    http.headers.update({"apikey": key, "Authorization": f"Bearer {key}"})
    try:
        try:
            if not flag_enabled(http):
                log(f"refused: feature flag '{FLAG_NAME}' is off (or missing).")
                return EXIT_REFUSED
            plan = build_plan(http)
        except (ExportError, httpx.HTTPError) as exc:
            log(f"cannot verify consent, nothing exported: {exc}")
            return EXIT_API

        log(
            f"uploads: {plan.uploads_total}; eligible: {len(plan.candidates)} photo(s) from "
            f"{plan.owners_eligible} consenting owner(s); skipped: "
            f"{plan.skipped_no_consent} without active consent, {plan.skipped_withdrawn} "
            f"withdrawn, {plan.skipped_bad_path} with invalid path"
        )
        if not args.execute:
            log("dry-run: nothing downloaded. Re-run with --execute to export.")
            log(STORAGE_ADVICE)
            return EXIT_OK

        log(STORAGE_ADVICE)
        stats = export_photos(http, plan, out_dir, allow_metadata=args.allow_metadata)
        log(
            "done: "
            + ", ".join(f"{k}={v}" for k, v in stats.items())
            + f". Manifest: {out_dir / MANIFEST_NAME}"
        )
        if stats["metadata"]:
            log("some photos were skipped because they still contain EXIF/XMP metadata.")
        return EXIT_PARTIAL if stats["error"] else EXIT_OK
    finally:
        if owns_client:
            http.close()


if __name__ == "__main__":
    raise SystemExit(main())
