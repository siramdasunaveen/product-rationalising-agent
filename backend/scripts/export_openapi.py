"""Write the OpenAPI document of the API to docs/openapi.json (the handover contract).

Run from the backend folder, no database needed:

    .\\.venv\\Scripts\\python.exe scripts\\export_openapi.py
"""

import json
import sys
from pathlib import Path

import fakeredis

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters.registry import build_platform  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"


def main() -> int:
    settings = Settings(platform_mode="standalone", fernet_key="", database_url="postgresql+psycopg://x:x@localhost/x")
    app = create_app(settings, platform=build_platform(settings, redis=fakeredis.FakeRedis()))
    spec = app.openapi()
    OUT.write_text(json.dumps(spec, indent=2, sort_keys=True), encoding="utf-8")
    paths = len(spec["paths"])
    ops = sum(len([m for m in p if m in ("get", "post", "put", "patch", "delete")]) for p in spec["paths"].values())
    print(f"wrote {OUT} ({paths} paths, {ops} operations)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
