"""Write the OpenAPI document to backend/api/openapi.json (deterministic).

    uv run python scripts/export_openapi.py          # write
    uv run python scripts/export_openapi.py --check  # exit 1 if the file is stale

The schema is generated from fixed, secret-free settings so the output does
not depend on the caller's environment.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.main import create_app  # noqa: E402
from app.repositories.memory import InMemoryRepository  # noqa: E402
from app.settings import Settings  # noqa: E402

OUTPUT = API_ROOT / "openapi.json"


def render() -> str:
    settings = Settings(SUPABASE_URL="https://example.supabase.co", API_JWKS_ENABLED=False)
    app = create_app(settings=settings, repo=InMemoryRepository(), configure_logs=False)
    return json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if openapi.json is stale")
    args = parser.parse_args(argv)
    content = render()
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != content:
            print("openapi.json is stale: run `uv run python scripts/export_openapi.py`")
            return 1
        print("openapi.json is up to date")
        return 0
    OUTPUT.write_text(content, encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
