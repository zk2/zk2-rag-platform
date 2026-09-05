"""Write the OpenAPI document and a standalone Redoc page.

Committed so the API can be read without running anything - which is the point
for a reviewer, and useful in a pull request where the diff shows exactly which
endpoints changed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REDOC_TEMPLATE = """<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>ZK2 RAG Platform API</title>
    <style>body {{ margin: 0; }}</style>
  </head>
  <body>
    <redoc spec-url="openapi.json"></redoc>
    <script src="https://cdn.jsdelivr.net/npm/redoc@2/bundles/redoc.standalone.js"></script>
  </body>
</html>
"""


def main(target: Path) -> None:
    # Imported here so the module can be read without booting the app
    from zk2.main import app  # noqa: PLC0415

    target.mkdir(parents=True, exist_ok=True)
    spec = app.openapi()
    (target / "openapi.json").write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    (target / "index.html").write_text(REDOC_TEMPLATE, encoding="utf-8")

    paths = len(spec.get("paths", {}))
    print(f"wrote {target}/openapi.json ({paths} paths) and {target}/index.html")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "../../docs/api"))
