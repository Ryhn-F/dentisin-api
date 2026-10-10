"""Regenerate the JSON examples embedded in docs/API.md by RUNNING the API.

    uv run python scripts/gen_api_examples.py          # rewrite the example blocks
    uv run python scripts/gen_api_examples.py --check  # exit 1 if docs/API.md is stale

Every example is produced in-process (FastAPI TestClient) with the test fixtures:
the in-memory repository, synthetic photo ids, the SHIPPED draft rules and, for the
detector, the TEST fake detector (no model ships). Only two fields are normalised so
the output is deterministic: ``request_id`` -> "<request_id>" and every
``duration_ms`` -> 0.0. Nothing else is edited by hand.

API.md marks each block with ``<!-- example:NAME -->`` followed by a ```json fence.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

import jwt  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.detector import Detection  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from tests.conftest import (  # noqa: E402
    ADMIN,
    GUARDIAN,
    HS_SECRET,
    IMG,
    IMG_NO_CONSENT,
    IMG_PATH,
    ISSUER,
    seeded_repo,
)
from tests.screening_support import (  # noqa: E402
    FakeDetector,
    analyze_body,
    evaluate_body,
    mouth_png,
    screening_settings,
    symptoms,
)

DOC = API_ROOT / "docs" / "API.md"
BLOCK = re.compile(r"(<!-- example:(?P<name>[a-z0-9_]+) -->\n```json\n)(?P<body>.*?)(\n```)", re.S)


def _token(sub: str) -> str:
    now = int(time.time())
    claims = {
        "sub": sub,
        "iat": now,
        "exp": now + 600,
        "aud": "authenticated",
        "iss": ISSUER,
        "role": "authenticated",
    }
    return jwt.encode(claims, HS_SECRET, algorithm="HS256")


def _normalise(node: Any) -> Any:
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key == "request_id":
                out[key] = "<request_id>"
            elif key == "duration_ms":
                out[key] = 0.0
            else:
                out[key] = _normalise(value)
        return out
    if isinstance(node, list):
        return [_normalise(v) for v in node]
    return node


def _client(**kwargs: Any) -> TestClient:
    repo = seeded_repo()
    repo.photos[IMG_PATH] = mouth_png()
    settings = screening_settings(ENABLE_CLOUD_INFER=True, API_JWKS_ENABLED=False)
    return TestClient(create_app(settings=settings, repo=repo, configure_logs=False, **kwargs))


def generate() -> dict[str, Any]:
    guardian = {"Authorization": f"Bearer {_token(GUARDIAN)}"}
    admin = {"Authorization": f"Bearer {_token(ADMIN)}"}
    fake = FakeDetector(
        [
            Detection("caries_primary", 112.0, 80.0, 160.0, 150.0, 0.81),
            Detection("caries_permanent", 170.0, 82.0, 214.0, 148.0, 0.34),
        ]
    )
    with_detector = _client(detector=fake)
    null_detector = _client()
    off = TestClient(
        create_app(
            settings=screening_settings(ENABLE_SCREENING_API=False, API_JWKS_ENABLED=False),
            repo=seeded_repo(),
            configure_logs=False,
        )
    )

    ex: dict[str, Any] = {}
    ev_req = evaluate_body(
        detections=[
            {"class": "caries_primary", "confidence": 0.81},
            {"class": "caries_permanent", "confidence": 0.34},
        ],
        symptoms=symptoms(white_spot="unknown"),
        age_months=96,
        pain=2,
        preferences={"allergies": ["flavor_mint"], "halal": True, "no_sls": False},
    )
    ex["evaluate_request"] = ev_req
    ex["evaluate_200"] = with_detector.post(
        "/v1/screening/evaluate", json=ev_req, headers=guardian
    ).json()
    box_req = evaluate_body(
        detections=[
            {"class": "caries_primary", "confidence": 0.81, "box": [112, 80, 160, 150]},
        ]
    )
    ex["evaluate_422_request"] = box_req
    ex["evaluate_422"] = with_detector.post(
        "/v1/screening/evaluate", json=box_req, headers=guardian
    ).json()

    rf_req = analyze_body(
        IMG, symptoms=symptoms(facial_swelling="yes"), age_months=None, pain="unknown"
    )
    ex["analyze_red_flag_request"] = rf_req
    ex["analyze_red_flag_200"] = with_detector.post(
        "/v1/screening/analyze", json=rf_req, headers=admin
    ).json()
    full_req = analyze_body(IMG, age_months=250, pain=0)
    ex["analyze_request"] = full_req
    ex["analyze_200"] = with_detector.post(
        "/v1/screening/analyze", json=full_req, headers=admin
    ).json()
    ex["analyze_503"] = null_detector.post(
        "/v1/screening/analyze", json=full_req, headers=admin
    ).json()
    ex["analyze_409"] = with_detector.post(
        "/v1/screening/analyze", json=analyze_body(IMG_NO_CONSENT), headers=admin
    ).json()
    ex["rules_200"] = with_detector.get("/v1/screening/rules", headers=guardian).json()
    ex["error_401"] = with_detector.get("/v1/screening/rules").json()
    ex["error_404"] = off.get("/v1/screening/rules", headers=guardian).json()
    return {name: _normalise(value) for name, value in ex.items()}


def render(doc: str, examples: dict[str, Any]) -> str:
    missing = set(examples) - {m.group("name") for m in BLOCK.finditer(doc)}
    if missing:
        raise SystemExit(f"docs/API.md lacks example markers: {sorted(missing)}")

    def _sub(match: re.Match[str]) -> str:
        name = match.group("name")
        if name not in examples:
            raise SystemExit(f"unknown example marker: {name}")
        body = json.dumps(examples[name], indent=2, ensure_ascii=False)
        return match.group(1) + body + match.group(4)

    return BLOCK.sub(_sub, doc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if docs/API.md is stale")
    args = parser.parse_args(argv)
    current = DOC.read_text(encoding="utf-8")
    updated = render(current, generate())
    if args.check:
        if updated != current:
            print("docs/API.md examples are stale: run `uv run python scripts/gen_api_examples.py`")
            return 1
        print("docs/API.md examples are up to date")
        return 0
    DOC.write_text(updated, encoding="utf-8", newline="\n")
    print(f"updated {DOC.relative_to(API_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
