"""Build ``dist/recommender.json`` (+ ``.sha256``) for the app, which has no Python.

``python -m recommender.build_bundle`` (write) or ``--check`` (fail if dist is stale).
The bundle is deterministic (sorted keys, no timestamp) so its hash only changes when the
rules or products change. Signing the hash into ``manifest.json`` is ``tools/sign_manifest.py``.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from recommender.release_check import collect_issues

_HERE = Path(__file__).resolve().parent
DIST = _HERE / "dist"
BUNDLE_NAME = "recommender.json"
BUNDLE_FORMAT = 1


def _canonical(data: Any) -> bytes:
    text = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (text + "\n").encode("utf-8")


def build() -> tuple[bytes, str]:
    """Return ``(bundle_bytes, sha256_hex)`` computed from the files on disk."""
    rules = json.loads((_HERE / "rules.json").read_text(encoding="utf-8"))
    products = json.loads((_HERE / "products.json").read_text(encoding="utf-8"))
    issues = collect_issues(rules, products)
    bundle = {
        "bundle_format": BUNDLE_FORMAT,
        "rules_version": rules["rules_version"],
        "products_version": products["products_version"],
        # The app must refuse to present this as clinical guidance while this is false.
        "release_ready": not issues,
        "release_issue_count": len(issues),
        "rules_sha256": hashlib.sha256(_canonical(rules)).hexdigest(),
        "products_sha256": hashlib.sha256(_canonical(products)).hexdigest(),
        "rules": rules,
        "products": products["products"],
    }
    payload = _canonical(bundle)
    return payload, hashlib.sha256(payload).hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    payload, digest = build()
    target = DIST / BUNDLE_NAME
    sha_line = f"{digest}  {BUNDLE_NAME}\n"
    sha_target = DIST / f"{BUNDLE_NAME}.sha256"
    if "--check" in args:
        fresh = (
            target.exists()
            and target.read_bytes() == payload
            and sha_target.exists()
            and sha_target.read_text(encoding="utf-8") == sha_line
        )
        print(
            "dist is up to date"
            if fresh
            else "dist is STALE: run python -m recommender.build_bundle"
        )
        return 0 if fresh else 1
    DIST.mkdir(exist_ok=True)
    target.write_bytes(payload)
    sha_target.write_text(sha_line, encoding="utf-8", newline="\n")
    print(f"wrote {target} ({len(payload)} bytes)\nsha256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
