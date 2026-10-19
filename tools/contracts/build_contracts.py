#!/usr/bin/env python3
"""Build and verify the versioned contract bundle for the app repo (IMPLEMENTATION_PLAN 4.3).

The app (separate repo) ports the triage engine, recommender, manifest verifier, detector decoder,
photo quality gate and brushing check-in to TypeScript. It must test those ports against the SAME
artifacts and parity vectors the Python references are tested with. This tool packs exactly the
files listed in ``contracts.json`` into one deterministic zip plus an index (``CONTRACTS.json``)
with per-file SHA-256, component versions and the clinical review status.

    python tools/contracts/build_contracts.py build                 # from committed HEAD
    python tools/contracts/build_contracts.py build --ref contracts-v0.1.0 --expect-version 0.1.0
    python tools/contracts/build_contracts.py build --worktree      # dev build, marked dirty
    python tools/contracts/build_contracts.py verify build/contracts/dentisai-contracts-0.1.0.zip

Release builds read file bytes from git objects (not the working copy), so the bundle is
byte-identical on every OS regardless of core.autocrlf, and reproducible for a given commit.
Stdlib only. Exit codes: 0 ok, 1 build/verify failure, 2 usage error.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
import sys
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

FORMAT = 1
INDEX_NAME = "CONTRACTS.json"
MAX_FILE_BYTES = 4 * 1024 * 1024
ZIP_DATE = (1980, 1, 1, 0, 0, 0)
ROLES = {"artifact", "schema", "checksum", "parity", "spec", "lint"}
PATH_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*(/[A-Za-z0-9_][A-Za-z0-9._-]*)*$")
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
SEMVER_RE = re.compile(r"^(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})$")
COMPONENT_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPEC = Path(__file__).resolve().parent / "contracts.json"

NOTICE = (
    "Contract bundle for parity tests and the app's bundled release. Clinical content is only "
    "usable on children when clinical_status is 'approved'; otherwise it is DRAFT/PLACEHOLDER "
    "and production builds must refuse it (the manifest signer refuses it as well)."
)


class ContractError(Exception):
    """Build or verification failure (message is safe to print: paths and codes only)."""


@dataclass(frozen=True)
class Entry:
    path: str
    component: str
    role: str
    test_only: bool


@dataclass(frozen=True)
class Spec:
    bundle_name: str
    bundle_version: str
    files: tuple[Entry, ...]


class Source(Protocol):
    def read(self, path: str) -> bytes: ...

    def describe(self) -> dict[str, Any]: ...


# --------------------------------------------------------------------------------------- spec
def load_spec(raw: bytes) -> Spec:
    try:
        obj = json.loads(raw)
    except ValueError as exc:
        raise ContractError("spec: not valid JSON") from exc
    if not isinstance(obj, dict):
        raise ContractError("spec: top level must be an object")
    name, version, files = obj.get("bundle_name"), obj.get("bundle_version"), obj.get("files")
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ContractError("spec: bundle_name invalid")
    if not isinstance(version, str) or not SEMVER_RE.match(version):
        raise ContractError("spec: bundle_version must be MAJOR.MINOR.PATCH")
    if not isinstance(files, list) or not files:
        raise ContractError("spec: files must be a non-empty list")
    entries: list[Entry] = []
    seen: set[str] = set()
    for i, f in enumerate(files):
        if not isinstance(f, dict) or set(f) - {"path", "component", "role", "test_only"}:
            raise ContractError(f"spec: files[{i}] has unknown keys")
        path, comp, role = f.get("path"), f.get("component"), f.get("role")
        test_only = f.get("test_only", False)
        if not isinstance(path, str) or not PATH_RE.match(path) or ".." in path.split("/"):
            raise ContractError(f"spec: files[{i}].path invalid")
        if path == INDEX_NAME or path in seen:
            raise ContractError(f"spec: duplicate or reserved path {path}")
        if not isinstance(comp, str) or not COMPONENT_RE.match(comp):
            raise ContractError(f"spec: files[{i}].component invalid")
        if role not in ROLES:
            raise ContractError(f"spec: files[{i}].role must be one of {sorted(ROLES)}")
        if not isinstance(test_only, bool):
            raise ContractError(f"spec: files[{i}].test_only must be boolean")
        seen.add(path)
        entries.append(Entry(path, comp, str(role), test_only))
    return Spec(name, version, tuple(entries))


# ------------------------------------------------------------------------------------ sources
def _git(root: Path, *args: str) -> bytes:
    try:
        res = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, check=False, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContractError("git not available") from exc
    if res.returncode != 0:
        raise ContractError(f"git {args[0]} failed")
    return res.stdout


class GitSource:
    """Committed bytes at ``ref`` (normalized line endings, independent of the working copy)."""

    def __init__(self, root: Path, ref: str) -> None:
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$", ref):
            raise ContractError("invalid --ref")
        self.root = root
        self.commit = _git(root, "rev-parse", "--verify", f"{ref}^{{commit}}").decode().strip()
        self.prefix = _git(root, "rev-parse", "--show-prefix").decode().strip()

    def read(self, path: str) -> bytes:
        try:
            return _git(self.root, "cat-file", "blob", f"{self.commit}:{self.prefix}{path}")
        except ContractError as exc:
            raise ContractError(f"missing in commit: {path}") from exc

    def describe(self) -> dict[str, Any]:
        return {"commit": self.commit, "dirty": False}


class WorktreeSource:
    """Working-copy bytes (development only; marked dirty, never released)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def read(self, path: str) -> bytes:
        p = self.root / path
        if not p.is_file():
            raise ContractError(f"missing file: {path}")
        return p.read_bytes()

    def describe(self) -> dict[str, Any]:
        return {"commit": "worktree", "dirty": True}


# ------------------------------------------------------------------------------- consistency
def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_checksum_file(entry: Entry, data: bytes, contents: dict[str, bytes]) -> None:
    """Every line ``<hex>  [*]<name>`` must match the bundled file next to the checksum file."""
    base = entry.path.rsplit("/", 1)[0] + "/" if "/" in entry.path else ""
    lines = [ln for ln in data.decode("ascii", "strict").splitlines() if ln.strip()]
    if not lines:
        raise ContractError(f"{entry.path}: empty checksum file")
    for ln in lines:
        m = re.match(r"^([0-9a-f]{64}) [ *]([A-Za-z0-9._-]+)$", ln)
        if not m:
            raise ContractError(f"{entry.path}: malformed line")
        target = base + m.group(2)
        if target not in contents:
            raise ContractError(f"{entry.path}: lists {target}, which is not in the bundle")
        if sha256(contents[target]) != m.group(1):
            raise ContractError(f"{entry.path}: checksum mismatch for {target} (rebuild dist)")


def _json(contents: dict[str, bytes], path: str) -> Any:
    try:
        return json.loads(contents[path])
    except (KeyError, ValueError) as exc:
        raise ContractError(f"{path}: missing or not JSON") from exc


def _yaml_scalar(contents: dict[str, bytes], path: str, key: str) -> str | None:
    """Top-level ``key: value`` of a YAML file (no YAML dependency; quotes stripped)."""
    text = contents[path].decode("utf-8")
    m = re.search(rf"^{re.escape(key)}:\s*\"?([^\"#\s]+)\"?\s*(#.*)?$", text, re.M)
    return m.group(1) if m else None


def _triage(c: dict[str, bytes]) -> dict[str, Any]:
    rules = _json(c, "triage/dist/rules.json")
    cases = _json(c, "triage/tests/cases.json")
    return {"rules_version": rules.get("version"), "status": rules.get("status"),
            "cases_format": cases.get("format_version"), "cases": len(cases.get("cases", []))}


def _recommender(c: dict[str, bytes]) -> dict[str, Any]:
    b = _json(c, "recommender/dist/recommender.json")
    cases = _json(c, "recommender/tests/cases.json")
    return {"rules_version": b.get("rules_version"), "release_ready": b.get("release_ready"),
            "release_issue_count": b.get("release_issue_count"),
            "cases_format": cases.get("format_version"), "cases": len(cases.get("cases", []))}


def _manifest(c: dict[str, bytes]) -> dict[str, Any]:
    m = re.search(rb"SPEC v(\d+)", c["tools/manifest/SPEC.md"])
    return {"spec_version": int(m.group(1)) if m else None}


def _detection(c: dict[str, bytes]) -> dict[str, Any]:
    v = _json(c, "ml/detection/tests/decode_vectors.json")
    schema = _json(c, "ml/detection/labels.schema.json")
    sv = schema.get("properties", {}).get("schema_version", {})
    return {"decode_spec_version": v.get("spec_version"),
            "labels_schema_version": sv.get("const", sv.get("enum"))}


def _quality_gate(c: dict[str, bytes]) -> dict[str, Any]:
    v = _json(c, "ml/detection/tests/quality_gate_vectors.json")
    return {"version": _yaml_scalar(c, "ml/detection/quality_gate.yaml", "version"),
            "status": _yaml_scalar(c, "ml/detection/quality_gate.yaml", "status"),
            "vectors_version": v.get("version"), "vectors": len(v.get("vectors", []))}


def _brushing(c: dict[str, bytes]) -> dict[str, Any]:
    cases = _json(c, "ml/brushing/tests/cases.json")
    return {"version": _yaml_scalar(c, "ml/brushing/brushing_rules.yaml", "version"),
            "status": _yaml_scalar(c, "ml/brushing/brushing_rules.yaml", "status"),
            "cases_schema": cases.get("schema")}


EXTRACTORS: dict[str, tuple[str, Callable[[dict[str, bytes]], dict[str, Any]]]] = {
    # component -> (file that must be present for extraction, extractor)
    "triage": ("triage/dist/rules.json", _triage),
    "recommender": ("recommender/dist/recommender.json", _recommender),
    "manifest": ("tools/manifest/SPEC.md", _manifest),
    "detection": ("ml/detection/tests/decode_vectors.json", _detection),
    "quality_gate": ("ml/detection/quality_gate.yaml", _quality_gate),
    "brushing": ("ml/brushing/brushing_rules.yaml", _brushing),
}


def clinical_blockers(components: dict[str, dict[str, Any]]) -> list[str]:
    """Components whose clinical content is not yet approved by the supervising dentist."""
    out: list[str] = []
    if "triage" in components and components["triage"].get("status") != "APPROVED":
        out.append("triage")
    if "recommender" in components and components["recommender"].get("release_ready") is not True:
        out.append("recommender")
    for name in ("quality_gate", "brushing"):
        if name in components and components[name].get("status") in (None, "PLACEHOLDER"):
            out.append(name)
    return out


# -------------------------------------------------------------------------------------- build
def dumps(obj: Any) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii")


def build(spec: Spec, source: Source) -> tuple[bytes, dict[str, Any]]:
    """Returns (zip bytes, index). Deterministic for equal spec + file bytes + source info."""
    contents: dict[str, bytes] = {}
    for e in spec.files:
        data = source.read(e.path)
        if not data:
            raise ContractError(f"{e.path}: empty file")
        if len(data) > MAX_FILE_BYTES:
            raise ContractError(f"{e.path}: larger than {MAX_FILE_BYTES} bytes")
        contents[e.path] = data
    for e in spec.files:
        if e.role == "checksum":
            check_checksum_file(e, contents[e.path], contents)

    present = {e.component for e in spec.files}
    components: dict[str, dict[str, Any]] = {}
    for name, (needed, fn) in EXTRACTORS.items():
        if name in present and needed in contents:
            components[name] = fn(contents)
    blockers = clinical_blockers(components)

    index: dict[str, Any] = {
        "format": FORMAT,
        "bundle_name": spec.bundle_name,
        "bundle_version": spec.bundle_version,
        "source": source.describe(),
        "clinical_status": "draft" if blockers else "approved",
        "clinical_blockers": blockers,
        "notice": NOTICE,
        "components": components,
        "files": [
            {"path": e.path, "component": e.component, "role": e.role, "test_only": e.test_only,
             "sha256": sha256(contents[e.path]), "size": len(contents[e.path])}
            for e in sorted(spec.files, key=lambda x: x.path)
        ],
    }
    root = f"{spec.bundle_name}-{spec.bundle_version}/"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        members = [(INDEX_NAME, dumps(index))] + sorted(contents.items())
        for name, data in members:
            info = zipfile.ZipInfo(root + name, date_time=ZIP_DATE)
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data, compresslevel=9)
    return buf.getvalue(), index


# ------------------------------------------------------------------------------------- verify
def verify(zip_bytes: bytes, expected_sha256: str | None = None) -> dict[str, Any]:
    """Checks a bundle the way the app repo should before using it. Returns the index."""
    if expected_sha256 is not None and sha256(zip_bytes) != expected_sha256:
        raise ContractError("bundle sha256 does not match the .sha256 sidecar")
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise ContractError("not a zip file") from exc
    with zf:
        names = zf.namelist()
        roots = {n.split("/", 1)[0] for n in names}
        if len(roots) != 1 or any("/" not in n or n.endswith("/") for n in names):
            raise ContractError("bundle must contain one top-level folder with files only")
        root = roots.pop() + "/"
        if len(names) != len(set(names)):
            raise ContractError("duplicate zip entries")
        try:
            index = json.loads(zf.read(root + INDEX_NAME))
        except (KeyError, ValueError) as exc:
            raise ContractError(f"{INDEX_NAME} missing or invalid") from exc
        if not isinstance(index, dict) or index.get("format") != FORMAT:
            raise ContractError(f"unsupported {INDEX_NAME} format")
        if root != f"{index.get('bundle_name')}-{index.get('bundle_version')}/":
            raise ContractError("folder name does not match bundle_name/bundle_version")
        listed: dict[str, dict[str, Any]] = {}
        for f in index.get("files", []):
            p = f.get("path")
            if not isinstance(p, str) or not PATH_RE.match(p) or ".." in p.split("/"):
                raise ContractError("index lists an invalid path")
            if not isinstance(f.get("sha256"), str) or not HEX64_RE.match(f["sha256"]):
                raise ContractError(f"index: bad sha256 for {p}")
            listed[p] = f
        actual = {n[len(root):] for n in names} - {INDEX_NAME}
        if actual != set(listed):
            extra, missing = sorted(actual - set(listed)), sorted(set(listed) - actual)
            raise ContractError(f"file set mismatch: extra={extra} missing={missing}")
        for p, f in listed.items():
            data = zf.read(root + p)
            if len(data) != f.get("size") or sha256(data) != f["sha256"]:
                raise ContractError(f"{p}: size/sha256 mismatch")
    return index


# ---------------------------------------------------------------------------------------- cli
def _cmd_build(args: argparse.Namespace) -> int:
    spec = load_spec(Path(args.spec).read_bytes())
    if args.expect_version and args.expect_version != spec.bundle_version:
        raise ContractError(
            f"tag/version mismatch: expected {args.expect_version}, spec has {spec.bundle_version}"
        )
    root = Path(args.root)
    source: Source = WorktreeSource(root) if args.worktree else GitSource(root, args.ref)
    data, index = build(spec, source)
    verify(data)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    name = f"{spec.bundle_name}-{spec.bundle_version}{'-dirty' if args.worktree else ''}.zip"
    (out / name).write_bytes(data)
    digest = sha256(data)
    (out / (name + ".sha256")).write_bytes(f"{digest}  {name}\n".encode("ascii"))
    (out / INDEX_NAME).write_bytes(dumps(index))
    print(json.dumps({"ok": True, "bundle": name, "sha256": digest, "files": len(index["files"]),
                      "source": index["source"], "clinical_status": index["clinical_status"],
                      "clinical_blockers": index["clinical_blockers"]}))
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    path = Path(args.bundle)
    expected = None
    sidecar = Path(str(path) + ".sha256")
    if sidecar.is_file():
        expected = sidecar.read_text(encoding="ascii").split()[0]
    index = verify(path.read_bytes(), expected)
    print(json.dumps({"ok": True, "bundle_version": index["bundle_version"],
                      "source": index["source"], "files": len(index["files"]),
                      "clinical_status": index["clinical_status"],
                      "sidecar_checked": expected is not None}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build the bundle zip + .sha256 + CONTRACTS.json")
    b.add_argument("--spec", default=str(DEFAULT_SPEC))
    b.add_argument("--root", default=str(BACKEND_ROOT), help="backend root (paths in the spec)")
    b.add_argument("--out", default=str(BACKEND_ROOT / "build" / "contracts"))
    g = b.add_mutually_exclusive_group()
    g.add_argument("--ref", default="HEAD", help="git ref to read committed bytes from")
    g.add_argument("--worktree", action="store_true", help="dev build from the working copy")
    b.add_argument("--expect-version", help="fail unless the spec bundle_version equals this")
    v = sub.add_parser("verify", help="verify a bundle zip (and its .sha256 sidecar if present)")
    v.add_argument("bundle")
    args = ap.parse_args(argv)
    try:
        return int(_cmd_build(args) if args.cmd == "build" else _cmd_verify(args))
    except (ContractError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
